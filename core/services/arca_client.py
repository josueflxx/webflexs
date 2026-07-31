"""ARCA WSAA/WSFE client restricted to homologation read-only access."""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import secrets
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

from django.conf import settings
from django.utils import timezone

from core.services.arca_config import (
    ArcaEndpointKind,
    ArcaSecurityConfigurationError,
    require_homologation_environment,
    resolve_arca_endpoint,
)
from core.services.arca_credentials import (
    ArcaCredentialError,
    resolve_credential_spec,
    validate_credential_offline,
)
from core.services.arca_homologation import (
    ArcaHomologationReadinessError,
    block_arca_emission,
    require_homologation_read_access,
)
from core.services.arca_ticket_cache import (
    ArcaAccessTicket,
    ArcaTicketCacheError,
    ArcaTicketCoordinator,
)
from core.services.arca_transport import (
    ArcaTransportError,
    StrictArcaSoapTransport,
)
from core.services.sensitive_data import sanitize_sensitive_text


class ArcaClientError(Exception):
    """Base ARCA error."""


class ArcaConfigurationError(ArcaClientError):
    """Missing or invalid ARCA setup."""


class ArcaTemporaryError(ArcaClientError):
    """ARCA/network issue with explicit delivery uncertainty."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "temporary_error",
        request_payload: Optional[Dict[str, Any]] = None,
        response_payload: Optional[Dict[str, Any]] = None,
        possibly_sent: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.request_payload = request_payload or {}
        self.response_payload = response_payload or {}
        self.possibly_sent = bool(possibly_sent)


@dataclass
class ArcaEmissionResult:
    """Normalized WSFE emission result."""

    state: str  # authorized | authorized_with_observations | uncertain | rejected
    error_code: str = ""
    error_message: str = ""
    cae: str = ""
    cae_due_date: Optional[date] = None
    request_payload: Optional[Dict[str, Any]] = None
    response_payload: Optional[Dict[str, Any]] = None
    observations: Optional[List[Dict[str, str]]] = None
    events: Optional[List[Dict[str, str]]] = None


@dataclass
class ArcaConsultationResult:
    """Normalized FECompConsultar result."""

    state: str  # authorized | not_found | error
    error_code: str = ""
    error_message: str = ""
    cae: str = ""
    cae_due_date: Optional[date] = None
    request_payload: Optional[Dict[str, Any]] = None
    response_payload: Optional[Dict[str, Any]] = None
    observations: Optional[List[Dict[str, str]]] = None
    events: Optional[List[Dict[str, str]]] = None


@dataclass(frozen=True)
class ArcaResponseMessages:
    errors: List[Dict[str, str]]
    observations: List[Dict[str, str]]
    events: List[Dict[str, str]]


DOC_TYPE_TO_CBTE_TYPE = {
    "FA": 1,   # Factura A
    "FB": 6,   # Factura B
    "FC": 11,  # Factura C
    "NCA": 3,  # Nota de credito A
    "NCB": 8,  # Nota de credito B
    "NCC": 13, # Nota de credito C
    "NDA": 2,  # Nota de debito A
    "NDB": 7,  # Nota de debito B
    "NDC": 12, # Nota de debito C
}

DOC_TYPE_TO_ARCA_DOC = {
    "cuit": 80,
    "cuil": 86,
    "dni": 96,
    "cdi": 87,
    "passport": 94,
    "otro": 99,
}


def _resolve_company_cfg(all_cfg: Dict[str, Any], company) -> Dict[str, Any]:
    """
    Resolve ARCA company config with tolerant key matching.
    Accepted keys:
    - exact slug (legacy behavior)
    - company id as string
    - case-insensitive slug match (e.g. Flexs/flexs)
    - case-insensitive id match
    """
    if not isinstance(all_cfg, dict):
        return {}

    slug = str(getattr(company, "slug", "") or "").strip()
    company_id = str(getattr(company, "id", "") or "").strip()

    if slug and isinstance(all_cfg.get(slug), dict):
        return all_cfg.get(slug) or {}
    if company_id and isinstance(all_cfg.get(company_id), dict):
        return all_cfg.get(company_id) or {}

    slug_l = slug.lower()
    id_l = company_id.lower()
    for key, value in all_cfg.items():
        if not isinstance(value, dict):
            continue
        key_l = str(key).strip().lower()
        if slug_l and key_l == slug_l:
            return value
        if id_l and key_l == id_l:
            return value
    return {}

IVA_RATE_TO_ID = {
    Decimal("0.00"): 3,
    Decimal("10.50"): 4,
    Decimal("21.00"): 5,
    Decimal("27.00"): 6,
    Decimal("5.00"): 8,
    Decimal("2.50"): 9,
}


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_first(node: ET.Element, name: str) -> Optional[ET.Element]:
    for child in node.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _find_all(node: ET.Element, name: str) -> List[ET.Element]:
    return [child for child in node.iter() if _local_name(child.tag) == name]


def _node_text(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    return (node.text or "").strip()


def _sanitize_digits(raw: str) -> str:
    return re.sub(r"\D+", "", str(raw or ""))


def _to_decimal(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw or 0))
    except Exception:
        return Decimal("0")


def _strict_decimal(raw: Any, *, field_name: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except Exception as exc:
        raise ArcaConfigurationError(
            f"Valor fiscal invalido para {field_name}."
        ) from exc
    if not value.is_finite():
        raise ArcaConfigurationError(
            f"Valor fiscal no finito para {field_name}."
        )
    return value


def _to_json_safe(value: Any):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(item) for item in value]
    return value


def _response_evidence(response_xml: str) -> Dict[str, Any]:
    encoded = str(response_xml or "").encode("utf-8", errors="replace")
    return {
        "response_sha256": hashlib.sha256(encoded).hexdigest(),
        "response_bytes": len(encoded),
    }


def _to_date_yyyymmdd(raw: str):
    value = str(raw or "").strip()
    if not value or len(value) != 8:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except Exception:
        return None


class ArcaWsfeClient:
    """Minimal WSAA + WSFEv1 client for FECAESolicitar."""

    WSAA_SOAP_ACTION = "loginCms"
    WSFE_SOAP_ACTION = "http://ar.gov.afip.dif.FEV1/FECAESolicitar"
    WSFE_LAST_AUTH_SOAP_ACTION = "http://ar.gov.afip.dif.FEV1/FECompUltimoAutorizado"
    WSFE_CONSULT_SOAP_ACTION = "http://ar.gov.afip.dif.FEV1/FECompConsultar"
    WSFE_DUMMY_SOAP_ACTION = "http://ar.gov.afip.dif.FEV1/FEDummy"
    WSFE_RECEIVER_VAT_SOAP_ACTION = (
        "http://ar.gov.afip.dif.FEV1/FEParamGetCondicionIvaReceptor"
    )
    WSFE_READONLY_PARAMETER_METHODS = frozenset(
        {
            "FEParamGetTiposCbte",
            "FEParamGetTiposDoc",
            "FEParamGetTiposIva",
            "FEParamGetTiposMonedas",
            "FEParamGetTiposConcepto",
            "FEParamGetPtosVenta",
        }
    )

    def __init__(
        self,
        *,
        company,
        point_of_sale,
        transport=None,
        credential_runner=None,
        ticket_coordinator=None,
        require_shared_cache: bool = True,
    ):
        self.company = company
        self.point_of_sale = point_of_sale
        self.timeout = int(getattr(settings, "ARCA_TIMEOUT_SECONDS", 30) or 30)
        self.openssl_bin = str(getattr(settings, "ARCA_OPENSSL_BIN", "openssl") or "openssl")
        self.service_name = str(
            getattr(settings, "ARCA_SERVICE_ID", "") or ""
        ).strip()
        try:
            self.environment_enum = require_homologation_environment(
                point_environment=getattr(point_of_sale, "environment", None),
            )
            self.wsaa_endpoint = resolve_arca_endpoint(
                self.environment_enum,
                ArcaEndpointKind.WSAA,
            )
            self.wsfe_endpoint = resolve_arca_endpoint(
                self.environment_enum,
                ArcaEndpointKind.WSFE,
            )
        except ArcaSecurityConfigurationError as exc:
            raise ArcaConfigurationError(str(exc)) from exc
        try:
            require_homologation_read_access(
                company=self.company,
                point_of_sale=self.point_of_sale,
            )
        except ArcaHomologationReadinessError as exc:
            raise ArcaConfigurationError(
                f"Compuerta ARCA no aprobada ({exc.error_code})."
            ) from exc
        self.environment = self.environment_enum.value
        self.wsaa_url = self.wsaa_endpoint.url
        self.wsfe_url = self.wsfe_endpoint.url

        try:
            self.credential_spec = resolve_credential_spec(
                company=self.company,
                environment=self.environment_enum,
            )
            validation_kwargs = {
                "openssl_bin": self.openssl_bin,
            }
            if credential_runner is not None:
                validation_kwargs["runner"] = credential_runner
            self.credential_metadata = validate_credential_offline(
                self.credential_spec,
                **validation_kwargs,
            )
        except ArcaCredentialError as exc:
            raise ArcaConfigurationError(
                f"Credencial ARCA invalida ({exc.error_code})."
            ) from exc

        self.issuer_cuit = self.credential_spec.issuer_cuit
        self.cert_path = str(self.credential_spec.cert_path)
        self.key_path = str(self.credential_spec.key_path)
        self.transport = transport or StrictArcaSoapTransport(timeout=self.timeout)
        try:
            self.ticket_coordinator = ticket_coordinator or ArcaTicketCoordinator(
                issuer_cuit=self.issuer_cuit,
                environment=self.environment,
                service=self.service_name,
                credential_fingerprint=self.credential_metadata.fingerprint_sha256,
                require_shared=require_shared_cache,
                lock_seconds=int(
                    getattr(settings, "ARCA_WSAA_LOCK_SECONDS", 60) or 60
                ),
                wait_seconds=float(
                    getattr(settings, "ARCA_WSAA_WAIT_SECONDS", 5) or 5
                ),
                renewal_margin_seconds=int(
                    getattr(
                        settings,
                        "ARCA_TA_RENEWAL_MARGIN_SECONDS",
                        120,
                    )
                    or 120
                ),
            )
        except ArcaTicketCacheError as exc:
            raise ArcaConfigurationError(str(exc)) from exc

    def _resolve_wsaa_url(self) -> str:
        return self.wsaa_endpoint.url

    def _resolve_wsfe_url(self) -> str:
        return self.wsfe_endpoint.url

    def _resolve_company_credentials(self):
        return self.issuer_cuit, self.cert_path, self.key_path

    def _build_tra(self) -> str:
        now_utc = datetime.now(dt_timezone.utc)
        generation = now_utc - timedelta(minutes=5)
        expiration = now_utc + timedelta(minutes=10)
        # WSAA requires an integer. A cryptographically random positive
        # 63-bit value avoids the cross-worker collisions caused by epoch
        # seconds while remaining inside a signed BIGINT.
        unique_id = secrets.randbelow((1 << 63) - 1) + 1
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<loginTicketRequest version=\"1.0\">"
            "<header>"
            f"<uniqueId>{unique_id}</uniqueId>"
            f"<generationTime>{generation.isoformat()}</generationTime>"
            f"<expirationTime>{expiration.isoformat()}</expirationTime>"
            "</header>"
            f"<service>{self.service_name}</service>"
            "</loginTicketRequest>"
        )

    def _sign_tra(self, tra_xml: str) -> str:
        fd_in, input_path = tempfile.mkstemp(prefix="arca-tra-", suffix=".xml")
        fd_out, output_path = tempfile.mkstemp(prefix="arca-cms-", suffix=".bin")
        os.close(fd_in)
        os.close(fd_out)
        try:
            with open(input_path, "w", encoding="utf-8") as handle:
                handle.write(tra_xml)

            cmd = [
                self.openssl_bin,
                "cms",
                "-sign",
                "-in",
                input_path,
                "-signer",
                self.cert_path,
                "-inkey",
                self.key_path,
                "-passin",
                "pass:",
                "-nodetach",
                "-outform",
                "DER",
                "-binary",
                "-out",
                output_path,
            ]
            try:
                process = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ArcaConfigurationError(
                    "No se pudo ejecutar la firma local del TRA."
                ) from exc
            if process.returncode != 0:
                raise ArcaConfigurationError(
                    "No se pudo firmar TRA con OpenSSL."
                )
            with open(output_path, "rb") as handle:
                cms = handle.read()
            return base64.b64encode(cms).decode("ascii")
        finally:
            for path in (input_path, output_path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _soap_post(
        self,
        *,
        url: str,
        soap_action: str,
        body_xml: str,
        possibly_sent_on_error: bool = False,
    ) -> str:
        try:
            require_homologation_read_access(
                company=self.company,
                point_of_sale=self.point_of_sale,
            )
        except ArcaHomologationReadinessError as exc:
            raise ArcaConfigurationError(
                f"Compuerta ARCA no aprobada ({exc.error_code})."
            ) from exc
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Header/>"
            f"<soapenv:Body>{body_xml}</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        if str(url or "") == self.wsaa_endpoint.url:
            endpoint = self.wsaa_endpoint
        elif str(url or "") == self.wsfe_endpoint.url:
            endpoint = self.wsfe_endpoint
        else:
            raise ArcaConfigurationError("Endpoint ARCA fuera de la tabla permitida.")
        try:
            response = self.transport.post(
                endpoint=endpoint,
                soap_action=soap_action,
                envelope=envelope.encode("utf-8"),
                possibly_sent_on_error=possibly_sent_on_error,
            )
            return response.text
        except ArcaSecurityConfigurationError as exc:
            raise ArcaConfigurationError(str(exc)) from exc
        except ArcaTransportError as exc:
            response_payload = {}
            if exc.response_body:
                response_payload = _response_evidence(
                    bytes(exc.response_body).decode("utf-8", errors="replace")
                )
            raise ArcaTemporaryError(
                sanitize_sensitive_text(str(exc)),
                error_code=exc.error_code,
                response_payload=response_payload,
                possibly_sent=bool(exc.possibly_sent),
            ) from exc

    def _login(self):
        try:
            ticket = self.ticket_coordinator.get_or_create(
                self._request_new_access_ticket
            )
        except ArcaTicketCacheError as exc:
            raise ArcaTemporaryError(
                str(exc),
                error_code=exc.error_code,
                possibly_sent=False,
            ) from exc
        return ticket.token, ticket.sign

    def _request_new_access_ticket(self) -> ArcaAccessTicket:
        tra_xml = self._build_tra()
        cms = self._sign_tra(tra_xml)
        body_xml = (
            '<ns1:loginCms xmlns:ns1="http://wsaa.view.sua.dvadac.desein.afip.gov">'
            f"<ns1:in0>{html.escape(cms)}</ns1:in0>"
            "</ns1:loginCms>"
        )
        response_xml = self._soap_post(
            url=self.wsaa_url,
            soap_action=self.WSAA_SOAP_ACTION,
            body_xml=body_xml,
            possibly_sent_on_error=False,
        )
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "Respuesta invalida de WSAA.",
                error_code="wsaa_parse_error",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            ) from exc

        login_return = _node_text(_find_first(root, "loginCmsReturn"))
        if not login_return:
            raise ArcaTemporaryError(
                "WSAA no devolvio loginCmsReturn.",
                error_code="wsaa_empty_response",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            )

        ticket_xml = html.unescape(login_return)
        try:
            ticket_root = ET.fromstring(ticket_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "No se pudo parsear ticket WSAA.",
                error_code="wsaa_ticket_parse_error",
                response_payload={
                    **_response_evidence(response_xml),
                    "ticket_sha256": hashlib.sha256(
                        ticket_xml.encode("utf-8", errors="replace")
                    ).hexdigest(),
                },
                possibly_sent=False,
            ) from exc

        token = _node_text(_find_first(ticket_root, "token"))
        sign = _node_text(_find_first(ticket_root, "sign"))
        generation = _node_text(_find_first(ticket_root, "generationTime"))
        expiration = _node_text(_find_first(ticket_root, "expirationTime"))
        if not token or not sign or not generation or not expiration:
            raise ArcaTemporaryError(
                "WSAA no devolvio un Ticket de Acceso completo.",
                error_code="wsaa_missing_credentials",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            )

        try:
            generated_at = datetime.fromisoformat(generation.replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=dt_timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=dt_timezone.utc)
            generated_at = generated_at.astimezone(dt_timezone.utc)
            expires_at = expires_at.astimezone(dt_timezone.utc)
        except Exception as exc:
            raise ArcaTemporaryError(
                "WSAA devolvio vigencia invalida.",
                error_code="wsaa_ticket_dates_invalid",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            ) from exc

        return ArcaAccessTicket(
            token=token,
            sign=sign,
            generation_time=generated_at,
            expiration_time=expires_at,
        )

    def _build_tax_breakdown(self, fiscal_document) -> List[Dict[str, Any]]:
        groups: Dict[Decimal, Dict[str, Decimal]] = {}
        items = list(fiscal_document.items.all())
        if not items:
            raise ArcaConfigurationError(
                "Documento fiscal sin items tributarios para emitir."
            )
        for item in items:
            rate = _strict_decimal(
                getattr(item, "iva_rate", None),
                field_name="alicuota IVA",
            ).quantize(Decimal("0.01"))
            arca_id = IVA_RATE_TO_ID.get(rate)
            if arca_id is None:
                raise ArcaConfigurationError(
                    f"Alicuota IVA no soportada por ARCA: {rate:.2f}%."
                )
            net = _strict_decimal(
                getattr(item, "net_amount", None),
                field_name="base imponible",
            ).quantize(Decimal("0.01"))
            tax = _strict_decimal(
                getattr(item, "iva_amount", None),
                field_name="importe IVA",
            ).quantize(Decimal("0.01"))
            if net < 0 or tax < 0:
                raise ArcaConfigurationError(
                    "Los importes tributarios no pueden ser negativos."
                )
            expected_tax = (net * rate / Decimal("100")).quantize(
                Decimal("0.01")
            )
            if abs(expected_tax - tax) > Decimal("0.01"):
                raise ArcaConfigurationError(
                    "El IVA de un item no reconcilia con su base y alicuota."
                )
            bucket = groups.setdefault(rate, {"base": Decimal("0.00"), "tax": Decimal("0.00")})
            bucket["base"] += net
            bucket["tax"] += tax

        iva_items = []
        for rate, bucket in sorted(groups.items()):
            iva_items.append(
                {
                    "id": IVA_RATE_TO_ID[rate],
                    "base": bucket["base"].quantize(Decimal("0.01")),
                    "tax": bucket["tax"].quantize(Decimal("0.01")),
                }
            )
        return iva_items

    def _build_wsfe_payload(self, *, fiscal_document, cbte_number: int, token: str, sign: str) -> Dict[str, Any]:
        persisted_payload = (
            fiscal_document.request_payload
            if isinstance(getattr(fiscal_document, "request_payload", None), dict)
            else {}
        )
        snapshot = persisted_payload.get("snapshot", {})
        client_snapshot = (
            snapshot.get("client", {}) if isinstance(snapshot, dict) else {}
        )
        if not isinstance(client_snapshot, dict) or not client_snapshot:
            raise ArcaConfigurationError(
                "Documento fiscal sin snapshot inmutable de receptor."
            )

        cbte_type = DOC_TYPE_TO_CBTE_TYPE.get(fiscal_document.doc_type)
        if not cbte_type:
            raise ArcaConfigurationError("Tipo de comprobante no soportado para ARCA en esta fase.")

        doc_type = DOC_TYPE_TO_ARCA_DOC.get(
            str(client_snapshot.get("document_type") or "").lower(),
        )
        if doc_type is None:
            raise ArcaConfigurationError(
                "Tipo de documento del receptor ausente o no soportado."
            )
        raw_doc_number = (
            client_snapshot.get("document_number")
            or "0"
        )
        doc_number = int(_sanitize_digits(raw_doc_number) or "0")

        iva_items = self._build_tax_breakdown(fiscal_document)
        imp_neto = sum(
            (row["base"] for row in iva_items),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))
        imp_iva = sum(
            (row["tax"] for row in iva_items),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))
        imp_total = _strict_decimal(
            getattr(fiscal_document, "total", None),
            field_name="total",
        ).quantize(Decimal("0.01"))
        document_tax = _strict_decimal(
            getattr(fiscal_document, "tax_total", None),
            field_name="total IVA",
        ).quantize(Decimal("0.01"))
        document_net = _strict_decimal(
            getattr(fiscal_document, "subtotal_net", None),
            field_name="subtotal neto",
        ).quantize(Decimal("0.01"))
        if min(imp_total, document_tax, document_net) < 0:
            raise ArcaConfigurationError(
                "Los totales fiscales no pueden ser negativos."
            )
        if imp_iva != document_tax or imp_neto != document_net:
            raise ArcaConfigurationError(
                "El desglose IVA no reconcilia con los totales congelados."
            )
        if imp_total != (imp_neto + imp_iva).quantize(Decimal("0.01")):
            raise ArcaConfigurationError(
                "El total fiscal no reconcilia con neto e IVA."
            )

        receiver_iva_condition_id = getattr(
            fiscal_document,
            "receiver_iva_condition_id_snapshot",
            None,
        )
        snapshot_condition = client_snapshot.get("iva_condition", {})
        if receiver_iva_condition_id is None and isinstance(snapshot_condition, dict):
            receiver_iva_condition_id = snapshot_condition.get("arca_id")
        try:
            receiver_iva_condition_id = int(receiver_iva_condition_id)
        except (TypeError, ValueError) as exc:
            raise ArcaConfigurationError(
                "Falta CondicionIVAReceptorId en el snapshot fiscal."
            ) from exc
        if receiver_iva_condition_id <= 0:
            raise ArcaConfigurationError(
                "CondicionIVAReceptorId invalida en el snapshot fiscal."
            )

        associated_documents = []
        if getattr(fiscal_document, "related_document_id", None):
            related_document = fiscal_document.related_document
            related_cbte_type = DOC_TYPE_TO_CBTE_TYPE.get(getattr(related_document, "doc_type", ""))
            related_pos = getattr(getattr(related_document, "point_of_sale", None), "number", "")
            related_number = getattr(related_document, "number", None)
            if not related_cbte_type or not related_pos or not related_number:
                raise ArcaConfigurationError(
                    "El comprobante relacionado no tiene datos suficientes para ARCA (tipo, punto de venta o numero)."
                )
            associated_documents.append(
                {
                    "tipo": int(related_cbte_type),
                    "pto_vta": int(related_pos),
                    "nro": int(related_number),
                }
            )

        fiscal_issued_at = getattr(fiscal_document, "issued_at", None)
        if isinstance(fiscal_issued_at, datetime):
            if timezone.is_naive(fiscal_issued_at):
                raise ArcaConfigurationError(
                    "La fecha fiscal congelada debe incluir zona horaria."
                )
            issue_date = timezone.localtime(fiscal_issued_at).date()
        elif isinstance(fiscal_issued_at, date):
            issue_date = fiscal_issued_at
        else:
            raise ArcaConfigurationError(
                "Falta fecha fiscal congelada para construir el request ARCA."
            )
        issue_date = issue_date.strftime("%Y%m%d")
        currency_code = str(getattr(fiscal_document, "currency", "ARS") or "ARS").upper()
        if currency_code == "ARS":
            currency_code = "PES"
        payload = {
            "auth": {
                "token": token,
                "sign": sign,
                "cuit": int(self.issuer_cuit),
            },
            "cabecera": {
                "cant_reg": 1,
                "pto_vta": int(self.point_of_sale.number),
                "cbte_tipo": cbte_type,
            },
            "detalle": {
                "concepto": 1,
                "doc_tipo": doc_type,
                "doc_nro": doc_number,
                "cbte_desde": cbte_number,
                "cbte_hasta": cbte_number,
                "cbte_fch": issue_date,
                "imp_total": imp_total,
                "imp_tot_conc": Decimal("0.00"),
                "imp_neto": imp_neto,
                "imp_op_ex": Decimal("0.00"),
                "imp_iva": imp_iva,
                "imp_trib": Decimal("0.00"),
                "mon_id": currency_code,
                "mon_cotiz": _strict_decimal(
                    getattr(fiscal_document, "exchange_rate", None),
                    field_name="cotizacion",
                ).quantize(Decimal("0.000001")),
                "iva": iva_items,
                "cbtes_asoc": associated_documents,
                "condicion_iva_receptor_id": receiver_iva_condition_id,
            },
        }
        return payload

    def _build_fe_cae_soap_body(self, payload: Dict[str, Any]) -> str:
        auth = payload["auth"]
        cab = payload["cabecera"]
        det = payload["detalle"]

        iva_xml = ""
        if det["iva"]:
            iva_rows = []
            for iva in det["iva"]:
                iva_rows.append(
                    "<AlicIva>"
                    f"<Id>{int(iva['id'])}</Id>"
                    f"<BaseImp>{iva['base']:.2f}</BaseImp>"
                    f"<Importe>{iva['tax']:.2f}</Importe>"
                    "</AlicIva>"
                )
            iva_xml = f"<Iva>{''.join(iva_rows)}</Iva>"

        associated_xml = ""
        if det.get("cbtes_asoc"):
            associated_rows = []
            for associated in det["cbtes_asoc"]:
                associated_rows.append(
                    "<CbteAsoc>"
                    f"<Tipo>{int(associated['tipo'])}</Tipo>"
                    f"<PtoVta>{int(associated['pto_vta'])}</PtoVta>"
                    f"<Nro>{int(associated['nro'])}</Nro>"
                    "</CbteAsoc>"
                )
            associated_xml = f"<CbtesAsoc>{''.join(associated_rows)}</CbtesAsoc>"

        return (
            '<FECAESolicitar xmlns="http://ar.gov.afip.dif.FEV1/">'
            "<Auth>"
            f"<Token>{html.escape(str(auth['token']))}</Token>"
            f"<Sign>{html.escape(str(auth['sign']))}</Sign>"
            f"<Cuit>{int(auth['cuit'])}</Cuit>"
            "</Auth>"
            "<FeCAEReq>"
            "<FeCabReq>"
            f"<CantReg>{int(cab['cant_reg'])}</CantReg>"
            f"<PtoVta>{int(cab['pto_vta'])}</PtoVta>"
            f"<CbteTipo>{int(cab['cbte_tipo'])}</CbteTipo>"
            "</FeCabReq>"
            "<FeDetReq>"
            "<FECAEDetRequest>"
            f"<Concepto>{int(det['concepto'])}</Concepto>"
            f"<DocTipo>{int(det['doc_tipo'])}</DocTipo>"
            f"<DocNro>{int(det['doc_nro'])}</DocNro>"
            f"<CbteDesde>{int(det['cbte_desde'])}</CbteDesde>"
            f"<CbteHasta>{int(det['cbte_hasta'])}</CbteHasta>"
            f"<CbteFch>{det['cbte_fch']}</CbteFch>"
            f"{associated_xml}"
            f"<ImpTotal>{det['imp_total']:.2f}</ImpTotal>"
            f"<ImpTotConc>{det['imp_tot_conc']:.2f}</ImpTotConc>"
            f"<ImpNeto>{det['imp_neto']:.2f}</ImpNeto>"
            f"<ImpOpEx>{det['imp_op_ex']:.2f}</ImpOpEx>"
            f"<ImpIVA>{det['imp_iva']:.2f}</ImpIVA>"
            f"<ImpTrib>{det['imp_trib']:.2f}</ImpTrib>"
            f"<MonId>{det['mon_id']}</MonId>"
            f"<MonCotiz>{det['mon_cotiz']:.6f}</MonCotiz>"
            f"{iva_xml}"
            f"<CondicionIVAReceptorId>{int(det['condicion_iva_receptor_id'])}</CondicionIVAReceptorId>"
            "</FECAEDetRequest>"
            "</FeDetReq>"
            "</FeCAEReq>"
            "</FECAESolicitar>"
        )

    def _build_last_authorized_soap_body(self, *, token: str, sign: str, cbte_type: int) -> str:
        return (
            '<FECompUltimoAutorizado xmlns="http://ar.gov.afip.dif.FEV1/">'
            "<Auth>"
            f"<Token>{html.escape(str(token))}</Token>"
            f"<Sign>{html.escape(str(sign))}</Sign>"
            f"<Cuit>{int(self.issuer_cuit)}</Cuit>"
            "</Auth>"
            f"<PtoVta>{int(self.point_of_sale.number)}</PtoVta>"
            f"<CbteTipo>{int(cbte_type)}</CbteTipo>"
            "</FECompUltimoAutorizado>"
        )

    def _build_consult_soap_body(
        self,
        *,
        token: str,
        sign: str,
        cbte_type: int,
        cbte_number: int,
    ) -> str:
        return (
            '<FECompConsultar xmlns="http://ar.gov.afip.dif.FEV1/">'
            "<Auth>"
            f"<Token>{html.escape(str(token))}</Token>"
            f"<Sign>{html.escape(str(sign))}</Sign>"
            f"<Cuit>{int(self.issuer_cuit)}</Cuit>"
            "</Auth>"
            "<FeCompConsReq>"
            f"<CbteTipo>{int(cbte_type)}</CbteTipo>"
            f"<CbteNro>{int(cbte_number)}</CbteNro>"
            f"<PtoVta>{int(self.point_of_sale.number)}</PtoVta>"
            "</FeCompConsReq>"
            "</FECompConsultar>"
        )

    @staticmethod
    def _build_dummy_soap_body() -> str:
        return '<FEDummy xmlns="http://ar.gov.afip.dif.FEV1/" />'

    def _build_receiver_vat_conditions_soap_body(
        self,
        *,
        token: str,
        sign: str,
    ) -> str:
        return (
            '<FEParamGetCondicionIvaReceptor xmlns="http://ar.gov.afip.dif.FEV1/">'
            "<Auth>"
            f"<Token>{html.escape(str(token))}</Token>"
            f"<Sign>{html.escape(str(sign))}</Sign>"
            f"<Cuit>{int(self.issuer_cuit)}</Cuit>"
            "</Auth>"
            "</FEParamGetCondicionIvaReceptor>"
        )

    def _build_authenticated_read_body(
        self,
        *,
        method: str,
        token: str,
        sign: str,
    ) -> str:
        if method not in self.WSFE_READONLY_PARAMETER_METHODS:
            raise ArcaConfigurationError(
                "Metodo parametrico WSFE no permitido."
            )
        return (
            f'<{method} xmlns="http://ar.gov.afip.dif.FEV1/">'
            "<Auth>"
            f"<Token>{html.escape(str(token))}</Token>"
            f"<Sign>{html.escape(str(sign))}</Sign>"
            f"<Cuit>{html.escape(str(self.issuer_cuit))}</Cuit>"
            "</Auth>"
            f"</{method}>"
        )

    def _parse_last_authorized_response(self, response_xml: str) -> int:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "No se pudo interpretar FECompUltimoAutorizado.",
                error_code="parse_last_authorized_error",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            ) from exc

        result_node = _find_first(root, "FECompUltimoAutorizadoResult")
        if result_node is None:
            fault = _find_first(root, "faultstring")
            fault_text = _node_text(fault) or "Respuesta SOAP sin FECompUltimoAutorizadoResult."
            raise ArcaTemporaryError(
                fault_text,
                error_code="soap_fault_last_authorized",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            )

        errors = self._extract_errors(result_node)
        if errors:
            first_error = errors[0]
            raise ArcaTemporaryError(
                first_error.get("msg", "") or "ARCA devolvio error consultando ultimo autorizado.",
                error_code=first_error.get("code", "") or "last_authorized_error",
                response_payload={
                    **_response_evidence(response_xml),
                    "errors": errors,
                },
                possibly_sent=False,
            )

        number_text = _node_text(_find_first(result_node, "CbteNro"))
        digits = _sanitize_digits(number_text)
        if not digits:
            return 0
        try:
            return int(digits)
        except Exception:
            return 0

    @staticmethod
    def _message_rows(node: ET.Element, tag_name: str) -> List[Dict[str, str]]:
        rows = []
        for item in _find_all(node, tag_name):
            rows.append(
                {
                    "code": _node_text(_find_first(item, "Code")),
                    "msg": sanitize_sensitive_text(
                        _node_text(_find_first(item, "Msg"))
                    ),
                }
            )
        return rows

    def _extract_messages(self, node: ET.Element) -> ArcaResponseMessages:
        return ArcaResponseMessages(
            errors=self._message_rows(node, "Err"),
            observations=self._message_rows(node, "Obs"),
            events=self._message_rows(node, "Evt"),
        )

    def _extract_errors(self, node: ET.Element) -> List[Dict[str, str]]:
        """Compatibility helper that no longer mixes observations into errors."""

        return self._extract_messages(node).errors

    def _parse_fe_cae_response(self, *, response_xml: str, request_payload: Dict[str, Any]) -> ArcaEmissionResult:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError:
            return ArcaEmissionResult(
                state="uncertain",
                error_code="parse_error",
                error_message="No se pudo interpretar respuesta ARCA.",
                request_payload=request_payload,
                response_payload=_response_evidence(response_xml),
                observations=[],
                events=[],
            )

        result_node = _find_first(root, "FECAESolicitarResult")
        if result_node is None:
            fault = _find_first(root, "faultstring")
            fault_text = sanitize_sensitive_text(
                _node_text(fault) or "Respuesta SOAP sin FECAESolicitarResult."
            )
            return ArcaEmissionResult(
                state="uncertain",
                error_code="soap_fault",
                error_message=fault_text,
                request_payload=request_payload,
                response_payload=_response_evidence(response_xml),
                observations=[],
                events=[],
            )

        detail = _find_first(result_node, "FECAEDetResponse")
        result_code = _node_text(_find_first(detail or result_node, "Resultado"))
        cae = _node_text(_find_first(detail or result_node, "CAE"))
        cae_due_raw = _node_text(_find_first(detail or result_node, "CAEFchVto"))
        cae_due_date = _to_date_yyyymmdd(cae_due_raw)
        messages = self._extract_messages(result_node)
        first_message = (
            messages.errors[0]
            if messages.errors
            else (
                messages.observations[0]
                if messages.observations
                else {"code": "", "msg": ""}
            )
        )

        response_payload = {
            **_response_evidence(response_xml),
            "result_code": result_code,
            "errors": messages.errors,
            "observations": messages.observations,
            "events": messages.events,
            "cae": cae,
            "cae_due_date": cae_due_raw,
        }

        if cae and result_code == "A":
            return ArcaEmissionResult(
                state=(
                    "authorized_with_observations"
                    if messages.observations
                    else "authorized"
                ),
                cae=cae,
                cae_due_date=cae_due_date,
                request_payload=request_payload,
                response_payload=response_payload,
                observations=messages.observations,
                events=messages.events,
            )

        if result_code == "R":
            return ArcaEmissionResult(
                state="rejected",
                error_code=first_message.get("code", "") or "rejected",
                error_message=first_message.get("msg", "") or "ARCA rechazo la emision.",
                request_payload=request_payload,
                response_payload=response_payload,
                observations=messages.observations,
                events=messages.events,
            )

        return ArcaEmissionResult(
            state="uncertain",
            error_code=first_message.get("code", "") or "uncertain_result",
            error_message=(
                first_message.get("msg", "")
                or "ARCA devolvio un resultado incompleto o no concluyente."
            ),
            request_payload=request_payload,
            response_payload=response_payload,
            observations=messages.observations,
            events=messages.events,
        )

    def emit_fiscal_document(
        self,
        *,
        fiscal_document,
        cbte_number: int,
        mark_dispatched=None,
    ) -> ArcaEmissionResult:
        # This stage permits only WSAA and WSFEv1 read operations. Keep the
        # block before login, payload construction and dispatch callbacks.
        block_arca_emission()
        token, sign = self._login()
        wsfe_payload = self._build_wsfe_payload(
            fiscal_document=fiscal_document,
            cbte_number=cbte_number,
            token=token,
            sign=sign,
        )
        request_payload = {
            "environment": self.environment,
            "wsaa_url": self.wsaa_url,
            "wsfe_url": self.wsfe_url,
            "point_of_sale": self.point_of_sale.number,
            "doc_type": fiscal_document.doc_type,
            "cbte_number": cbte_number,
            "payload": {
                "auth": {
                    "cuit": wsfe_payload["auth"]["cuit"],
                },
                "cabecera": wsfe_payload["cabecera"],
                "detalle": {
                    **wsfe_payload["detalle"],
                    "iva": wsfe_payload["detalle"]["iva"],
                },
            },
        }
        request_payload = _to_json_safe(request_payload)
        body = self._build_fe_cae_soap_body(wsfe_payload)
        if callable(mark_dispatched):
            # Persist the uncertainty boundary before the transport can send a
            # byte. A process death after this point must be query-only.
            mark_dispatched()
        try:
            response_xml = self._soap_post(
                url=self.wsfe_url,
                soap_action=self.WSFE_SOAP_ACTION,
                body_xml=body,
                possibly_sent_on_error=True,
            )
        except ArcaTemporaryError as exc:
            if not exc.possibly_sent:
                raise
            return ArcaEmissionResult(
                state="uncertain",
                error_code=exc.error_code or "uncertain_transport",
                error_message=(
                    sanitize_sensitive_text(str(exc))
                    or "Resultado de autorizacion incierto."
                ),
                request_payload=request_payload,
                response_payload=exc.response_payload or {},
                observations=[],
                events=[],
            )
        return self._parse_fe_cae_response(
            response_xml=response_xml,
            request_payload=request_payload,
        )

    def _parse_consult_response(
        self,
        *,
        response_xml: str,
        request_payload: Dict[str, Any],
    ) -> ArcaConsultationResult:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError:
            return ArcaConsultationResult(
                state="error",
                error_code="consult_parse_error",
                error_message="No se pudo interpretar FECompConsultar.",
                request_payload=request_payload,
                response_payload=_response_evidence(response_xml),
                observations=[],
                events=[],
            )

        result_node = _find_first(root, "FECompConsultarResult")
        if result_node is None:
            fault = sanitize_sensitive_text(
                _node_text(_find_first(root, "faultstring"))
            )
            return ArcaConsultationResult(
                state="error",
                error_code="consult_soap_fault",
                error_message=fault or "Respuesta SOAP sin FECompConsultarResult.",
                request_payload=request_payload,
                response_payload=_response_evidence(response_xml),
                observations=[],
                events=[],
            )

        messages = self._extract_messages(result_node)
        result_get = _find_first(result_node, "ResultGet")
        authorization_code = _node_text(
            _find_first(result_get or result_node, "CodAutorizacion")
        ) or _node_text(_find_first(result_get or result_node, "CAE"))
        due_raw = _node_text(
            _find_first(result_get or result_node, "FchVto")
        ) or _node_text(_find_first(result_get or result_node, "CAEFchVto"))
        result_code = _node_text(
            _find_first(result_get or result_node, "Resultado")
        )
        evidence = {
            **_response_evidence(response_xml),
            "result_code": result_code,
            "cae": authorization_code,
            "cae_due_date": due_raw,
            "errors": messages.errors,
            "observations": messages.observations,
            "events": messages.events,
        }
        if authorization_code and result_code in {"", "A"}:
            return ArcaConsultationResult(
                state="authorized",
                cae=authorization_code,
                cae_due_date=_to_date_yyyymmdd(due_raw),
                request_payload=request_payload,
                response_payload=evidence,
                observations=messages.observations,
                events=messages.events,
            )

        not_found_codes = {"602"}
        not_found = result_get is None and not messages.errors
        if messages.errors and all(
            str(item.get("code") or "") in not_found_codes
            for item in messages.errors
        ):
            not_found = True
        if not_found:
            return ArcaConsultationResult(
                state="not_found",
                request_payload=request_payload,
                response_payload=evidence,
                observations=messages.observations,
                events=messages.events,
            )

        first_error = messages.errors[0] if messages.errors else {}
        return ArcaConsultationResult(
            state="error",
            error_code=first_error.get("code", "") or "consult_inconclusive",
            error_message=(
                first_error.get("msg", "")
                or "FECompConsultar no devolvio un resultado concluyente."
            ),
            request_payload=request_payload,
            response_payload=evidence,
            observations=messages.observations,
            events=messages.events,
        )

    def consult_fiscal_document(
        self,
        *,
        doc_type: str,
        cbte_number: int,
    ) -> ArcaConsultationResult:
        cbte_type = DOC_TYPE_TO_CBTE_TYPE.get(str(doc_type or "").strip().upper())
        if not cbte_type:
            return ArcaConsultationResult(
                state="error",
                error_code="unsupported_document_type",
                error_message="Tipo fiscal no soportado para FECompConsultar.",
                request_payload={},
                response_payload={},
                observations=[],
                events=[],
            )
        try:
            number = int(cbte_number)
        except (TypeError, ValueError):
            number = 0
        if number <= 0:
            return ArcaConsultationResult(
                state="error",
                error_code="invalid_document_number",
                error_message="Numero fiscal invalido para FECompConsultar.",
                request_payload={},
                response_payload={},
                observations=[],
                events=[],
            )

        request_payload = {
            "environment": self.environment,
            "point_of_sale": int(self.point_of_sale.number),
            "doc_type": str(doc_type or "").strip().upper(),
            "cbte_type": int(cbte_type),
            "cbte_number": number,
        }
        try:
            token, sign = self._login()
            body = self._build_consult_soap_body(
                token=token,
                sign=sign,
                cbte_type=int(cbte_type),
                cbte_number=number,
            )
            response_xml = self._soap_post(
                url=self.wsfe_url,
                soap_action=self.WSFE_CONSULT_SOAP_ACTION,
                body_xml=body,
                possibly_sent_on_error=False,
            )
        except ArcaTemporaryError as exc:
            return ArcaConsultationResult(
                state="error",
                error_code=exc.error_code or "consult_transport_error",
                error_message=sanitize_sensitive_text(str(exc)),
                request_payload=request_payload,
                response_payload=exc.response_payload or {},
                observations=[],
                events=[],
            )
        return self._parse_consult_response(
            response_xml=response_xml,
            request_payload=request_payload,
        )

    def fetch_service_status(self) -> Dict[str, Any]:
        response_xml = self._soap_post(
            url=self.wsfe_url,
            soap_action=self.WSFE_DUMMY_SOAP_ACTION,
            body_xml=self._build_dummy_soap_body(),
            possibly_sent_on_error=False,
        )
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "No se pudo interpretar FEDummy.",
                error_code="dummy_parse_error",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            ) from exc
        result = _find_first(root, "FEDummyResult")
        if result is None:
            raise ArcaTemporaryError(
                "FEDummy no devolvio resultado.",
                error_code="dummy_missing_result",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            )
        servers = {
            "app": _node_text(_find_first(result, "AppServer")),
            "db": _node_text(_find_first(result, "DbServer")),
            "auth": _node_text(_find_first(result, "AuthServer")),
        }
        return {
            "ok": all(value.upper() == "OK" for value in servers.values()),
            "servers": servers,
            **_response_evidence(response_xml),
        }

    # Explicit alias matching the official method name.
    fedummy = fetch_service_status

    def fetch_receiver_vat_conditions(self) -> Dict[str, Any]:
        token, sign = self._login()
        response_xml = self._soap_post(
            url=self.wsfe_url,
            soap_action=self.WSFE_RECEIVER_VAT_SOAP_ACTION,
            body_xml=self._build_receiver_vat_conditions_soap_body(
                token=token,
                sign=sign,
            ),
            possibly_sent_on_error=False,
        )
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "No se pudo interpretar FEParamGetCondicionIvaReceptor.",
                error_code="receiver_vat_conditions_parse_error",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            ) from exc
        result = _find_first(root, "FEParamGetCondicionIvaReceptorResult")
        if result is None:
            raise ArcaTemporaryError(
                "La consulta de condiciones IVA no devolvio resultado.",
                error_code="receiver_vat_conditions_missing_result",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            )
        messages = self._extract_messages(result)
        if messages.errors:
            first = messages.errors[0]
            raise ArcaTemporaryError(
                first.get("msg", "") or "ARCA rechazo la consulta de condiciones IVA.",
                error_code=first.get("code", "") or "receiver_vat_conditions_error",
                response_payload={
                    **_response_evidence(response_xml),
                    "errors": messages.errors,
                    "observations": messages.observations,
                    "events": messages.events,
                },
                possibly_sent=False,
            )
        values = []
        for row in _find_all(result, "CondicionIvaReceptor"):
            raw_id = _node_text(_find_first(row, "Id"))
            try:
                condition_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            values.append(
                {
                    "id": condition_id,
                    "description": _node_text(_find_first(row, "Desc")),
                    "document_classes": _node_text(
                        _find_first(row, "Cmp_Clase")
                    ),
                }
            )
        if not values:
            raise ArcaTemporaryError(
                "ARCA no devolvio condiciones IVA utilizables.",
                error_code="receiver_vat_conditions_empty",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            )
        return {
            "values": values,
            "observations": messages.observations,
            "events": messages.events,
            **_response_evidence(response_xml),
        }

    def fetch_parameter_catalog(self, *, method: str) -> Dict[str, Any]:
        """Run one allowlisted WSFEv1 parameter query and sanitize its result."""

        method_name = str(method or "").strip()
        if method_name not in self.WSFE_READONLY_PARAMETER_METHODS:
            raise ArcaConfigurationError(
                "Metodo parametrico WSFE no permitido."
            )
        token, sign = self._login()
        response_xml = self._soap_post(
            url=self.wsfe_url,
            soap_action=f"http://ar.gov.afip.dif.FEV1/{method_name}",
            body_xml=self._build_authenticated_read_body(
                method=method_name,
                token=token,
                sign=sign,
            ),
            possibly_sent_on_error=False,
        )
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "No se pudo interpretar la consulta parametrica WSFE.",
                error_code="parameter_catalog_parse_error",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            ) from exc

        result = _find_first(root, f"{method_name}Result")
        if result is None:
            raise ArcaTemporaryError(
                "La consulta parametrica WSFE no devolvio resultado.",
                error_code="parameter_catalog_missing_result",
                response_payload=_response_evidence(response_xml),
                possibly_sent=False,
            )
        messages = self._extract_messages(result)
        if messages.errors:
            first = messages.errors[0]
            raise ArcaTemporaryError(
                sanitize_sensitive_text(
                    first.get("msg", "")
                    or "ARCA rechazo la consulta parametrica."
                ),
                error_code=first.get("code", "") or "parameter_catalog_error",
                response_payload={
                    **_response_evidence(response_xml),
                    "errors": messages.errors,
                    "events": messages.events,
                },
                possibly_sent=False,
            )

        result_get = _find_first(result, "ResultGet")
        values: list[Dict[str, str]] = []
        if result_get is not None:
            for row in list(result_get):
                row_data = {
                    _local_name(field.tag): _node_text(field)
                    for field in list(row)
                }
                if row_data:
                    values.append(row_data)
        return {
            "method": method_name,
            "values": values,
            "observations": messages.observations,
            "events": messages.events,
            **_response_evidence(response_xml),
        }

    def fetch_readonly_catalogs(self) -> Dict[str, Dict[str, Any]]:
        methods = {
            "voucher_types": "FEParamGetTiposCbte",
            "document_types": "FEParamGetTiposDoc",
            "vat_rates": "FEParamGetTiposIva",
            "currencies": "FEParamGetTiposMonedas",
            "concepts": "FEParamGetTiposConcepto",
            "points_of_sale": "FEParamGetPtosVenta",
        }
        return {
            label: self.fetch_parameter_catalog(method=method)
            for label, method in methods.items()
        }

    def fetch_points_of_sale(self) -> Dict[str, Any]:
        return self.fetch_parameter_catalog(method="FEParamGetPtosVenta")

    def fetch_last_authorized_number(self, *, doc_type: str) -> int:
        cbte_type = DOC_TYPE_TO_CBTE_TYPE.get(str(doc_type or "").strip().upper())
        if not cbte_type:
            raise ArcaConfigurationError(
                f"Tipo fiscal {doc_type} no soportado para FECompUltimoAutorizado."
            )

        return self.fetch_last_authorized_by_type(cbte_type=int(cbte_type))

    def fetch_last_authorized_by_type(self, *, cbte_type: int) -> int:
        try:
            normalized_type = int(cbte_type)
        except (TypeError, ValueError) as exc:
            raise ArcaConfigurationError(
                "Tipo de comprobante invalido para FECompUltimoAutorizado."
            ) from exc
        if not 0 < normalized_type <= 999:
            raise ArcaConfigurationError(
                "Tipo de comprobante invalido para FECompUltimoAutorizado."
            )

        token, sign = self._login()
        body = self._build_last_authorized_soap_body(
            token=token,
            sign=sign,
            cbte_type=normalized_type,
        )
        response_xml = self._soap_post(
            url=self.wsfe_url,
            soap_action=self.WSFE_LAST_AUTH_SOAP_ACTION,
            body_xml=body,
        )
        return self._parse_last_authorized_response(response_xml=response_xml)

    def run_preflight(self) -> Dict[str, Any]:
        """
        Run only the approved WSAA/WSFEv1 read operations for the exact
        configured point of sale and voucher type.
        """
        token, sign = self._login()
        service_status = self.fetch_service_status()
        if not service_status.get("ok"):
            raise ArcaTemporaryError(
                "FEDummy no confirmo disponibilidad de homologacion."
            )
        catalogs = self.fetch_readonly_catalogs()
        configured_point = int(getattr(settings, "ARCA_PTO_VTA", 0) or 0)
        configured_voucher_type = int(
            getattr(settings, "ARCA_DEFAULT_CBTE_TIPO", 0) or 0
        )
        point_values = catalogs["points_of_sale"].get("values", [])
        voucher_values = catalogs["voucher_types"].get("values", [])
        point_found = any(
            str(row.get("Nro") or "").isdigit()
            and int(row["Nro"]) == configured_point
            for row in point_values
        )
        voucher_type_found = any(
            str(row.get("Id") or "").isdigit()
            and int(row["Id"]) == configured_voucher_type
            for row in voucher_values
        )
        if not point_found:
            raise ArcaConfigurationError(
                "El punto de venta configurado no fue confirmado por WSFEv1."
            )
        if not voucher_type_found:
            raise ArcaConfigurationError(
                "El tipo de comprobante configurado no fue confirmado por WSFEv1."
            )
        last_authorized = self.fetch_last_authorized_by_type(
            cbte_type=configured_voucher_type
        )
        checks = {
            "token_obtained": bool(token),
            "sign_obtained": bool(sign),
            "configured_point_found": point_found,
            "configured_voucher_type_found": voucher_type_found,
        }
        return {
            "ok": bool(
                service_status.get("ok")
                and token
                and sign
                and point_found
                and voucher_type_found
                and last_authorized >= 0
            ),
            "environment": self.environment,
            "company_id": self.company.id,
            "point_of_sale": self.point_of_sale.number,
            "voucher_type": configured_voucher_type,
            "checks": checks,
            "service_status": service_status,
            "catalog_counts": {
                label: len(result.get("values", []))
                for label, result in catalogs.items()
            },
            "last_authorized_number": last_authorized,
        }
