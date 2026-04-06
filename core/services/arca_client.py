"""ARCA WSAA/WSFE client (homologation-first, production-ready)."""

from __future__ import annotations

import base64
import html
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


class ArcaClientError(Exception):
    """Base ARCA error."""


class ArcaConfigurationError(ArcaClientError):
    """Missing or invalid ARCA setup."""


class ArcaTemporaryError(ArcaClientError):
    """Temporary ARCA/network issue; document can move to pending_retry."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "temporary_error",
        request_payload: Optional[Dict[str, Any]] = None,
        response_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.request_payload = request_payload or {}
        self.response_payload = response_payload or {}


@dataclass
class ArcaEmissionResult:
    """Normalized WSFE emission result."""

    state: str  # authorized | pending_retry | rejected
    error_code: str = ""
    error_message: str = ""
    cae: str = ""
    cae_due_date: Optional[date] = None
    request_payload: Optional[Dict[str, Any]] = None
    response_payload: Optional[Dict[str, Any]] = None


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

    def __init__(self, *, company, point_of_sale):
        self.company = company
        self.point_of_sale = point_of_sale
        self.environment = (point_of_sale.environment or "homologation").strip().lower()
        self.timeout = int(getattr(settings, "ARCA_TIMEOUT_SECONDS", 30) or 30)
        self.openssl_bin = str(getattr(settings, "ARCA_OPENSSL_BIN", "openssl") or "openssl")
        self.service_name = str(getattr(settings, "ARCA_WSAA_SERVICE", "wsfe") or "wsfe")
        self.allow_production = bool(getattr(settings, "ARCA_ALLOW_PRODUCTION", False))

        if self.environment == "production" and not self.allow_production:
            raise ArcaConfigurationError(
                "Produccion ARCA deshabilitada. Habilita ARCA_ALLOW_PRODUCTION para emitir en produccion."
            )

        self.wsaa_url = self._resolve_wsaa_url()
        self.wsfe_url = self._resolve_wsfe_url()
        self.issuer_cuit, self.cert_path, self.key_path = self._resolve_company_credentials()

    def _resolve_wsaa_url(self) -> str:
        if self.environment == "production":
            return str(getattr(settings, "ARCA_WSAA_URL_PRODUCTION", "") or "").strip()
        return str(getattr(settings, "ARCA_WSAA_URL_HOMOLOGATION", "") or "").strip()

    def _resolve_wsfe_url(self) -> str:
        if self.environment == "production":
            return str(getattr(settings, "ARCA_WSFE_URL_PRODUCTION", "") or "").strip()
        return str(getattr(settings, "ARCA_WSFE_URL_HOMOLOGATION", "") or "").strip()

    def _resolve_company_credentials(self):
        all_cfg = getattr(settings, "ARCA_COMPANY_CONFIG", {}) or {}
        company_cfg = _resolve_company_cfg(all_cfg, self.company)
        env_cfg = {}
        if isinstance(company_cfg, dict):
            env_cfg = company_cfg.get(self.environment, {}) if self.environment in company_cfg else company_cfg
        if not isinstance(env_cfg, dict):
            env_cfg = {}

        issuer_cuit = _sanitize_digits(env_cfg.get("cuit") or self.company.cuit)
        cert_path = str(env_cfg.get("cert_path") or "").strip()
        key_path = str(env_cfg.get("key_path") or "").strip()

        if not issuer_cuit:
            raise ArcaConfigurationError("Falta CUIT emisor para ARCA en configuracion de empresa.")
        if not cert_path or not os.path.exists(cert_path):
            raise ArcaConfigurationError("Certificado ARCA no configurado o inexistente.")
        if not key_path or not os.path.exists(key_path):
            raise ArcaConfigurationError("Clave privada ARCA no configurada o inexistente.")
        if not self.wsaa_url or not self.wsfe_url:
            raise ArcaConfigurationError("URLs ARCA no configuradas en settings.")

        return issuer_cuit, cert_path, key_path

    def _build_tra(self) -> str:
        now_utc = datetime.now(dt_timezone.utc)
        generation = now_utc - timedelta(minutes=5)
        expiration = now_utc + timedelta(minutes=10)
        unique_id = int(now_utc.timestamp())
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
                "-nodetach",
                "-outform",
                "DER",
                "-binary",
                "-out",
                output_path,
            ]
            process = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )
            if process.returncode != 0:
                stderr = (process.stderr or "").strip()
                raise ArcaConfigurationError(
                    f"No se pudo firmar TRA con OpenSSL. {stderr or 'Sin detalle'}"
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

    def _soap_post(self, *, url: str, soap_action: str, body_xml: str) -> str:
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Header/>"
            f"<soapenv:Body>{body_xml}</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        req = Request(
            url=url,
            data=envelope.encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": soap_action,
            },
        )
        try:
            with urlopen(req, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            raise ArcaTemporaryError(
                f"HTTP ARCA {exc.code}",
                error_code=f"http_{exc.code}",
                response_payload={"raw": detail},
            ) from exc
        except URLError as exc:
            raise ArcaTemporaryError(
                f"No se pudo conectar con ARCA: {exc.reason}",
                error_code="network_error",
            ) from exc
        except Exception as exc:
            raise ArcaTemporaryError(
                f"Fallo inesperado enviando SOAP a ARCA: {exc}",
                error_code="soap_error",
            ) from exc

    def _get_cached_ticket(self):
        cache_key = f"arca:wsaa:{self.company.id}:{self.environment}"
        raw = cache.get(cache_key)
        if not raw:
            return None
        expires_at = raw.get("expires_at")
        if not expires_at:
            return None
        try:
            expires = datetime.fromisoformat(expires_at)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=dt_timezone.utc)
        except Exception:
            return None
        if expires <= datetime.now(dt_timezone.utc) + timedelta(seconds=120):
            return None
        return raw

    def _cache_ticket(self, *, token: str, sign: str, expires_at: datetime):
        cache_key = f"arca:wsaa:{self.company.id}:{self.environment}"
        now = datetime.now(dt_timezone.utc)
        ttl = int((expires_at - now).total_seconds()) - 120
        if ttl < 60:
            ttl = 60
        cache.set(
            cache_key,
            {"token": token, "sign": sign, "expires_at": expires_at.isoformat()},
            timeout=ttl,
        )

    def _login(self):
        cached = self._get_cached_ticket()
        if cached:
            return cached["token"], cached["sign"]

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
        )
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "Respuesta invalida de WSAA.",
                error_code="wsaa_parse_error",
                response_payload={"raw": response_xml},
            ) from exc

        login_return = _node_text(_find_first(root, "loginCmsReturn"))
        if not login_return:
            raise ArcaTemporaryError(
                "WSAA no devolvio loginCmsReturn.",
                error_code="wsaa_empty_response",
                response_payload={"raw": response_xml},
            )

        ticket_xml = html.unescape(login_return)
        try:
            ticket_root = ET.fromstring(ticket_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "No se pudo parsear ticket WSAA.",
                error_code="wsaa_ticket_parse_error",
                response_payload={"raw": response_xml, "ticket": ticket_xml},
            ) from exc

        token = _node_text(_find_first(ticket_root, "token"))
        sign = _node_text(_find_first(ticket_root, "sign"))
        expiration = _node_text(_find_first(ticket_root, "expirationTime"))
        if not token or not sign:
            raise ArcaTemporaryError(
                "WSAA no devolvio token/sign validos.",
                error_code="wsaa_missing_credentials",
                response_payload={"raw": response_xml, "ticket": ticket_xml},
            )

        try:
            expires_at = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=dt_timezone.utc)
        except Exception:
            expires_at = datetime.now(dt_timezone.utc) + timedelta(minutes=8)

        self._cache_ticket(token=token, sign=sign, expires_at=expires_at)
        return token, sign

    def _build_tax_breakdown(self, fiscal_document) -> List[Dict[str, Any]]:
        groups: Dict[Decimal, Dict[str, Decimal]] = {}
        for item in fiscal_document.items.all():
            rate = _to_decimal(getattr(item, "iva_rate", 0)).quantize(Decimal("0.01"))
            if rate <= Decimal("0"):
                continue
            net = _to_decimal(getattr(item, "net_amount", 0)).quantize(Decimal("0.01"))
            tax = _to_decimal(getattr(item, "iva_amount", 0)).quantize(Decimal("0.01"))
            bucket = groups.setdefault(rate, {"base": Decimal("0.00"), "tax": Decimal("0.00")})
            bucket["base"] += net
            bucket["tax"] += tax

        iva_items = []
        for rate, bucket in groups.items():
            arca_id = IVA_RATE_TO_ID.get(rate)
            if not arca_id:
                continue
            iva_items.append(
                {
                    "id": arca_id,
                    "base": bucket["base"].quantize(Decimal("0.01")),
                    "tax": bucket["tax"].quantize(Decimal("0.01")),
                }
            )
        return iva_items

    def _build_wsfe_payload(self, *, fiscal_document, cbte_number: int, token: str, sign: str) -> Dict[str, Any]:
        client_profile = fiscal_document.client_profile
        if not client_profile and fiscal_document.client_company_ref_id:
            client_profile = fiscal_document.client_company_ref.client_profile
        if not client_profile:
            raise ArcaConfigurationError("Documento fiscal sin cliente para emitir.")

        cbte_type = DOC_TYPE_TO_CBTE_TYPE.get(fiscal_document.doc_type)
        if not cbte_type:
            raise ArcaConfigurationError("Tipo de comprobante no soportado para ARCA en esta fase.")

        doc_type = DOC_TYPE_TO_ARCA_DOC.get(
            str(getattr(client_profile, "document_type", "") or "").lower(),
            99,
        )
        raw_doc_number = (
            getattr(client_profile, "document_number", "")
            or getattr(client_profile, "cuit_dni", "")
            or "0"
        )
        doc_number = int(_sanitize_digits(raw_doc_number) or "0")

        imp_total = _to_decimal(fiscal_document.total).quantize(Decimal("0.01"))
        imp_iva = _to_decimal(fiscal_document.tax_total).quantize(Decimal("0.01"))
        imp_neto = (imp_total - imp_iva).quantize(Decimal("0.01"))
        if imp_neto < Decimal("0"):
            imp_neto = Decimal("0.00")

        iva_items = self._build_tax_breakdown(fiscal_document)
        if not iva_items and imp_iva > 0:
            iva_items = [
                {
                    "id": IVA_RATE_TO_ID[Decimal("21.00")],
                    "base": imp_neto,
                    "tax": imp_iva,
                }
            ]

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

        issue_date = timezone.localdate().strftime("%Y%m%d")
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
                "mon_cotiz": _to_decimal(getattr(fiscal_document, "exchange_rate", 1)).quantize(Decimal("0.000001")),
                "iva": iva_items,
                "cbtes_asoc": associated_documents,
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

    def _parse_last_authorized_response(self, response_xml: str) -> int:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "No se pudo interpretar FECompUltimoAutorizado.",
                error_code="parse_last_authorized_error",
                response_payload={"raw": response_xml},
            ) from exc

        result_node = _find_first(root, "FECompUltimoAutorizadoResult")
        if result_node is None:
            fault = _find_first(root, "faultstring")
            fault_text = _node_text(fault) or "Respuesta SOAP sin FECompUltimoAutorizadoResult."
            raise ArcaTemporaryError(
                fault_text,
                error_code="soap_fault_last_authorized",
                response_payload={"raw": response_xml},
            )

        errors = self._extract_errors(result_node)
        if errors:
            first_error = errors[0]
            raise ArcaTemporaryError(
                first_error.get("msg", "") or "ARCA devolvio error consultando ultimo autorizado.",
                error_code=first_error.get("code", "") or "last_authorized_error",
                response_payload={"raw": response_xml, "errors": errors},
            )

        number_text = _node_text(_find_first(result_node, "CbteNro"))
        digits = _sanitize_digits(number_text)
        if not digits:
            return 0
        try:
            return int(digits)
        except Exception:
            return 0

    def _extract_errors(self, node: ET.Element) -> List[Dict[str, str]]:
        errors = []
        for err in _find_all(node, "Err"):
            code = _node_text(_find_first(err, "Code"))
            msg = _node_text(_find_first(err, "Msg"))
            errors.append({"code": code, "msg": msg})
        for obs in _find_all(node, "Obs"):
            code = _node_text(_find_first(obs, "Code"))
            msg = _node_text(_find_first(obs, "Msg"))
            errors.append({"code": code, "msg": msg})
        return errors

    def _parse_fe_cae_response(self, *, response_xml: str, request_payload: Dict[str, Any]) -> ArcaEmissionResult:
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError:
            return ArcaEmissionResult(
                state="pending_retry",
                error_code="parse_error",
                error_message="No se pudo interpretar respuesta ARCA.",
                request_payload=request_payload,
                response_payload={"raw": response_xml},
            )

        result_node = _find_first(root, "FECAESolicitarResult")
        if result_node is None:
            fault = _find_first(root, "faultstring")
            fault_text = _node_text(fault) or "Respuesta SOAP sin FECAESolicitarResult."
            return ArcaEmissionResult(
                state="pending_retry",
                error_code="soap_fault",
                error_message=fault_text,
                request_payload=request_payload,
                response_payload={"raw": response_xml},
            )

        detail = _find_first(result_node, "FECAEDetResponse")
        result_code = _node_text(_find_first(detail or result_node, "Resultado"))
        cae = _node_text(_find_first(detail or result_node, "CAE"))
        cae_due_raw = _node_text(_find_first(detail or result_node, "CAEFchVto"))
        cae_due_date = _to_date_yyyymmdd(cae_due_raw)
        errors = self._extract_errors(result_node)
        first_error = errors[0] if errors else {"code": "", "msg": ""}

        response_payload = {
            "raw": response_xml,
            "result_code": result_code,
            "errors": errors,
            "cae": cae,
            "cae_due_date": cae_due_raw,
        }

        if cae and result_code == "A":
            return ArcaEmissionResult(
                state="authorized",
                cae=cae,
                cae_due_date=cae_due_date,
                request_payload=request_payload,
                response_payload=response_payload,
            )

        if result_code == "P":
            return ArcaEmissionResult(
                state="pending_retry",
                error_code=first_error.get("code", "") or "pending",
                error_message=first_error.get("msg", "") or "ARCA devolvio estado pendiente.",
                request_payload=request_payload,
                response_payload=response_payload,
            )

        return ArcaEmissionResult(
            state="rejected",
            error_code=first_error.get("code", "") or "rejected",
            error_message=first_error.get("msg", "") or "ARCA rechazo la emision.",
            request_payload=request_payload,
            response_payload=response_payload,
        )

    def emit_fiscal_document(self, *, fiscal_document, cbte_number: int) -> ArcaEmissionResult:
        token, sign = self._login()
        wsfe_payload = self._build_wsfe_payload(
            fiscal_document=fiscal_document,
            cbte_number=cbte_number,
            token=token,
            sign=sign,
        )
        body = self._build_fe_cae_soap_body(wsfe_payload)
        response_xml = self._soap_post(
            url=self.wsfe_url,
            soap_action=self.WSFE_SOAP_ACTION,
            body_xml=body,
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
                    "token_preview": f"{str(token)[:12]}...",
                    "sign_preview": f"{str(sign)[:12]}...",
                },
                "cabecera": wsfe_payload["cabecera"],
                "detalle": {
                    **wsfe_payload["detalle"],
                    "iva": wsfe_payload["detalle"]["iva"],
                },
            },
        }
        return self._parse_fe_cae_response(
            response_xml=response_xml,
            request_payload=_to_json_safe(request_payload),
        )

    def fetch_last_authorized_number(self, *, doc_type: str) -> int:
        cbte_type = DOC_TYPE_TO_CBTE_TYPE.get(str(doc_type or "").strip().upper())
        if not cbte_type:
            raise ArcaConfigurationError(
                f"Tipo fiscal {doc_type} no soportado para FECompUltimoAutorizado."
            )

        token, sign = self._login()
        body = self._build_last_authorized_soap_body(
            token=token,
            sign=sign,
            cbte_type=int(cbte_type),
        )
        response_xml = self._soap_post(
            url=self.wsfe_url,
            soap_action=self.WSFE_LAST_AUTH_SOAP_ACTION,
            body_xml=body,
        )
        return self._parse_last_authorized_response(response_xml=response_xml)

    def run_preflight(self) -> Dict[str, Any]:
        """
        Validate credentials + connectivity using WSAA login and one WSFE
        last-number lookup for the current point of sale.
        """
        token, sign = self._login()
        checks = {
            "token_obtained": bool(token),
            "sign_obtained": bool(sign),
        }
        last_numbers = {}
        for doc_type in ("FA", "FB", "FC"):
            try:
                last_numbers[doc_type] = self.fetch_last_authorized_number(doc_type=doc_type)
            except Exception:
                last_numbers[doc_type] = None
        return {
            "ok": True,
            "environment": self.environment,
            "company_id": self.company.id,
            "point_of_sale": self.point_of_sale.number,
            "checks": checks,
            "last_authorized_numbers": last_numbers,
        }
