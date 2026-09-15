"""Direct homologation-only client for ARCA registration-proof lookup."""

from __future__ import annotations

import hashlib
import html
import unicodedata
from typing import Any, Dict, List, Mapping, Optional
import xml.etree.ElementTree as ET

from django.conf import settings

from accounts.fiscal_identity import is_valid_cuit, normalize_fiscal_document
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
from core.services.arca_errors import (
    ArcaConfigurationError,
    ArcaTemporaryError,
)
from core.services.arca_homologation import (
    ArcaHomologationReadinessError,
    require_taxpayer_registry_read_access,
)
from core.services.arca_ticket_cache import (
    ArcaTicketCacheError,
    ArcaTicketCoordinator,
)
from core.services.arca_transport import (
    ArcaTransportError,
    StrictArcaSoapTransport,
)
from core.services.arca_wsaa import ArcaWsaaSessionMixin
from core.services.fiscal_provider import (
    TaxpayerLookupResult,
    TaxpayerLookupStatus,
)
from core.services.sensitive_data import sanitize_sensitive_text


TAXPAYER_SERVICE_ID = "ws_sr_constancia_inscripcion"
TAXPAYER_SOAP_NAMESPACE = "http://a5.soap.ws.server.puc.sr/"
TAXPAYER_SOURCE = "arca_direct_ws_sr_constancia_inscripcion"


def _local_name(tag: str) -> str:
    return str(tag or "").split("}")[-1].split(":")[-1]


def _find_first(node: Optional[ET.Element], *names: str):
    if node is None:
        return None
    expected = set(names)
    for candidate in node.iter():
        if _local_name(candidate.tag) in expected:
            return candidate
    return None


def _find_all(node: Optional[ET.Element], name: str) -> List[ET.Element]:
    if node is None:
        return []
    return [
        candidate
        for candidate in node.iter()
        if _local_name(candidate.tag) == name
    ]


def _text(node: Optional[ET.Element]) -> str:
    return str(getattr(node, "text", "") or "").strip()


def _child_text(node: Optional[ET.Element], name: str) -> str:
    if node is None:
        return ""
    for child in list(node):
        if _local_name(child.tag) == name:
            return _text(child)
    return ""


def _element_data(node: Optional[ET.Element]) -> Dict[str, Any]:
    if node is None:
        return {}
    data: Dict[str, Any] = {}
    for child in list(node):
        name = _local_name(child.tag)
        value: Any = _element_data(child) if list(child) else _text(child)
        if name not in data:
            data[name] = value
        elif isinstance(data[name], list):
            data[name].append(value)
        else:
            data[name] = [data[name], value]
    return data


def _normalized_message(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).lower()


def _error_message(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    preferred = (
        _child_text(node, "mensaje")
        or _child_text(node, "message")
        or _child_text(node, "error")
        or _child_text(node, "descripcion")
    )
    if preferred:
        return preferred
    return " ".join(
        value.strip()
        for value in node.itertext()
        if str(value or "").strip()
    )


def _rows(node: Optional[ET.Element], item_name: str, *, source: str = ""):
    result = []
    for item in _find_all(node, item_name):
        row = _element_data(item)
        if source:
            row["source_section"] = source
        result.append(row)
    return tuple(result)


def parse_taxpayer_response(
    response_xml: str,
    *,
    requested_cuit: str,
) -> TaxpayerLookupResult:
    """Normalize optional/partial getPersona_v2 output without raw XML."""

    requested = normalize_fiscal_document(requested_cuit)
    try:
        root = ET.fromstring(str(response_xml or ""))
    except ET.ParseError as exc:
        raise ArcaTemporaryError(
            "Respuesta invalida del servicio de constancia.",
            error_code="taxpayer_parse_error",
            response_payload={
                "response_sha256": hashlib.sha256(
                    str(response_xml or "").encode("utf-8", errors="replace")
                ).hexdigest(),
            },
            possibly_sent=False,
        ) from exc

    fault = _find_first(root, "Fault")
    if fault is not None:
        raise ArcaTemporaryError(
            "El servicio de constancia devolvio un SOAP Fault.",
            error_code="taxpayer_soap_fault",
            response_payload={
                "response_sha256": hashlib.sha256(
                    str(response_xml or "").encode("utf-8", errors="replace")
                ).hexdigest(),
            },
            possibly_sent=False,
        )

    payload = _find_first(root, "personaReturn", "return")
    if payload is None:
        payload = root
    error_constancia = _find_first(payload, "errorConstancia")
    error_regimen = _find_first(payload, "errorRegimenGeneral")
    error_monotributo = _find_first(payload, "errorMonotributo")
    constancia_message = _error_message(error_constancia)
    section_warnings = tuple(
        message
        for message in (
            constancia_message,
            _error_message(error_regimen),
            _error_message(error_monotributo),
        )
        if message
    )

    if "no existe persona con ese id" in _normalized_message(constancia_message):
        return TaxpayerLookupResult(
            status=TaxpayerLookupStatus.NOT_FOUND,
            cuit=requested,
            warnings=(constancia_message,),
            source=TAXPAYER_SOURCE,
            raw_metadata={
                "response_sha256": hashlib.sha256(
                    str(response_xml or "").encode("utf-8", errors="replace")
                ).hexdigest(),
            },
        )

    general = _find_first(payload, "datosGenerales")
    if general is None:
        raise ArcaTemporaryError(
            "La constancia no devolvio datos generales interpretables.",
            error_code="taxpayer_missing_general_data",
            response_payload={
                "response_sha256": hashlib.sha256(
                    str(response_xml or "").encode("utf-8", errors="replace")
                ).hexdigest(),
            },
            possibly_sent=False,
        )

    returned_cuit = normalize_fiscal_document(_child_text(general, "idPersona"))
    if returned_cuit and returned_cuit != requested:
        raise ArcaTemporaryError(
            "La identidad devuelta no coincide con la CUIT consultada.",
            error_code="taxpayer_identity_mismatch",
            possibly_sent=False,
        )

    person_type = _child_text(general, "tipoPersona").upper()
    key_status = _child_text(general, "estadoClave").upper()
    first_name = _child_text(general, "nombre")
    last_name = _child_text(general, "apellido")
    business_name = _child_text(general, "razonSocial")
    if person_type == "JURIDICA" and business_name:
        display_name = business_name
    else:
        display_name = " ".join(
            part for part in (first_name, last_name) if part
        ).strip() or business_name

    regime = _find_first(payload, "datosRegimenGeneral")
    monotributo_node = _find_first(payload, "datosMonotributo")
    taxes = (
        *_rows(regime, "impuesto", source="regimen_general"),
        *_rows(monotributo_node, "impuesto", source="monotributo"),
    )
    activities = (
        *_rows(regime, "actividad", source="regimen_general"),
        *_rows(monotributo_node, "actividad", source="monotributo"),
    )
    partial = bool(section_warnings)
    if key_status == "INACTIVO":
        status = TaxpayerLookupStatus.INACTIVE
    elif partial:
        status = TaxpayerLookupStatus.PARTIAL
    else:
        status = TaxpayerLookupStatus.FOUND

    metadata = _element_data(_find_first(payload, "metadata"))
    metadata.update(
        {
            "response_sha256": hashlib.sha256(
                str(response_xml or "").encode("utf-8", errors="replace")
            ).hexdigest(),
            "schema": "getPersona_v2",
        }
    )

    return TaxpayerLookupResult(
        status=status,
        cuit=returned_cuit or requested,
        person_type=person_type,
        key_status=key_status,
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        business_name=business_name,
        fiscal_address=_element_data(_find_first(general, "domicilioFiscal")),
        general_data=_element_data(general),
        general_regime=(
            _element_data(regime) if regime is not None else None
        ),
        taxes=taxes,
        activities=activities,
        monotributo=(
            _element_data(monotributo_node)
            if monotributo_node is not None
            else None
        ),
        characterizations=_rows(general, "caracterizacion"),
        warnings=section_warnings,
        source=TAXPAYER_SOURCE,
        raw_metadata=metadata,
        is_partial=partial,
    )


class ArcaTaxpayerRegistryClient(ArcaWsaaSessionMixin):
    """Read-only direct client. It exposes no voucher authorization method."""

    WSAA_SOAP_ACTION = "loginCms"
    # The WSCI SOAP 1.1 binding publishes an empty action.
    TAXPAYER_SOAP_ACTION = ""

    def __init__(
        self,
        *,
        company,
        transport=None,
        credential_runner=None,
        ticket_coordinator=None,
        require_shared_cache: bool = True,
    ) -> None:
        self.company = company
        self.timeout = int(getattr(settings, "ARCA_TIMEOUT_SECONDS", 30) or 30)
        self.openssl_bin = str(
            getattr(settings, "ARCA_OPENSSL_BIN", "openssl") or "openssl"
        )
        self.service_name = str(
            getattr(settings, "ARCA_TAXPAYER_SERVICE_ID", "") or ""
        ).strip()
        try:
            self.environment_enum = require_homologation_environment()
            self.wsaa_endpoint = resolve_arca_endpoint(
                self.environment_enum,
                ArcaEndpointKind.WSAA,
            )
            self.taxpayer_endpoint = resolve_arca_endpoint(
                self.environment_enum,
                ArcaEndpointKind.TAXPAYER_REGISTRY,
            )
            require_taxpayer_registry_read_access(company=company)
        except (
            ArcaSecurityConfigurationError,
            ArcaHomologationReadinessError,
        ) as exc:
            code = getattr(exc, "error_code", "configuration")
            raise ArcaConfigurationError(
                f"Compuerta ARCA de padron no aprobada ({code})."
            ) from exc

        if self.service_name != TAXPAYER_SERVICE_ID:
            raise ArcaConfigurationError(
                "Service ID de constancia no permitido."
            )

        self.environment = self.environment_enum.value
        self.wsaa_url = self.wsaa_endpoint.url
        self.taxpayer_url = self.taxpayer_endpoint.url
        try:
            self.credential_spec = resolve_credential_spec(
                company=self.company,
                environment=self.environment_enum,
            )
            validation_kwargs = {"openssl_bin": self.openssl_bin}
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
                credential_fingerprint=(
                    self.credential_metadata.fingerprint_sha256
                ),
                require_shared=require_shared_cache,
                lock_seconds=int(
                    getattr(settings, "ARCA_WSAA_LOCK_SECONDS", 60) or 60
                ),
                wait_seconds=float(
                    getattr(settings, "ARCA_WSAA_WAIT_SECONDS", 5) or 5
                ),
                renewal_margin_seconds=int(
                    getattr(settings, "ARCA_TA_RENEWAL_MARGIN_SECONDS", 120)
                    or 120
                ),
            )
        except ArcaTicketCacheError as exc:
            raise ArcaConfigurationError(str(exc)) from exc

    @staticmethod
    def _response_evidence(response_xml: str) -> Dict[str, Any]:
        encoded = str(response_xml or "").encode("utf-8", errors="replace")
        return {
            "response_sha256": hashlib.sha256(encoded).hexdigest(),
            "response_bytes": len(encoded),
        }

    def _soap_post(
        self,
        *,
        url: str,
        soap_action: str,
        body_xml: str,
        possibly_sent_on_error: bool = False,
    ) -> str:
        try:
            require_taxpayer_registry_read_access(company=self.company)
        except ArcaHomologationReadinessError as exc:
            raise ArcaConfigurationError(
                f"Compuerta ARCA de padron no aprobada ({exc.error_code})."
            ) from exc
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope '
            'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Header/>"
            f"<soapenv:Body>{body_xml}</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        if str(url or "") == self.wsaa_endpoint.url:
            endpoint = self.wsaa_endpoint
        elif str(url or "") == self.taxpayer_endpoint.url:
            endpoint = self.taxpayer_endpoint
        else:
            raise ArcaConfigurationError(
                "Endpoint ARCA fuera de la tabla permitida."
            )
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
            evidence = (
                self._response_evidence(
                    bytes(exc.response_body).decode("utf-8", errors="replace")
                )
                if exc.response_body
                else {}
            )
            raise ArcaTemporaryError(
                sanitize_sensitive_text(str(exc)),
                error_code=exc.error_code,
                response_payload=evidence,
                possibly_sent=bool(exc.possibly_sent),
            ) from exc

    @staticmethod
    def _build_dummy_body() -> str:
        return (
            f'<tns:dummy xmlns:tns="{TAXPAYER_SOAP_NAMESPACE}" />'
        )

    @staticmethod
    def _build_get_persona_body(
        *,
        token: str,
        sign: str,
        represented_cuit: str,
        taxpayer_cuit: str,
    ) -> str:
        return (
            f'<tns:getPersona_v2 xmlns:tns="{TAXPAYER_SOAP_NAMESPACE}">'
            f"<token>{html.escape(str(token))}</token>"
            f"<sign>{html.escape(str(sign))}</sign>"
            f"<cuitRepresentada>{html.escape(str(represented_cuit))}</cuitRepresentada>"
            f"<idPersona>{html.escape(str(taxpayer_cuit))}</idPersona>"
            "</tns:getPersona_v2>"
        )

    def fetch_service_status(self) -> Mapping[str, Any]:
        response_xml = self._soap_post(
            url=self.taxpayer_url,
            soap_action=self.TAXPAYER_SOAP_ACTION,
            body_xml=self._build_dummy_body(),
        )
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ArcaTemporaryError(
                "Respuesta dummy invalida del servicio de constancia.",
                error_code="taxpayer_dummy_parse_error",
                response_payload=self._response_evidence(response_xml),
                possibly_sent=False,
            ) from exc
        values = {
            name: _text(_find_first(root, name))
            for name in ("appserver", "authserver", "dbserver")
        }
        values["ok"] = all(
            str(values[name] or "").upper() == "OK"
            for name in ("appserver", "authserver", "dbserver")
        )
        return values

    def lookup_taxpayer(self, cuit: str) -> TaxpayerLookupResult:
        taxpayer_cuit = normalize_fiscal_document(cuit)
        if not is_valid_cuit(taxpayer_cuit):
            raise ValueError("CUIT a consultar invalida.")
        token, sign = self._login()
        body = self._build_get_persona_body(
            token=token,
            sign=sign,
            represented_cuit=self.issuer_cuit,
            taxpayer_cuit=taxpayer_cuit,
        )
        response_xml = self._soap_post(
            url=self.taxpayer_url,
            soap_action=self.TAXPAYER_SOAP_ACTION,
            body_xml=body,
            possibly_sent_on_error=False,
        )
        return parse_taxpayer_response(
            response_xml,
            requested_cuit=taxpayer_cuit,
        )
