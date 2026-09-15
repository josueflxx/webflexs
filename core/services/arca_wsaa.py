"""Reusable WSAA session primitives shared by every direct ARCA service."""

from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET

from core.services.arca_errors import (
    ArcaConfigurationError,
    ArcaTemporaryError,
)
from core.services.arca_ticket_cache import (
    ArcaAccessTicket,
    ArcaTicketCacheError,
)


def _local_name(tag: str) -> str:
    return str(tag or "").split("}")[-1].split(":")[-1]


def _find_first(node: ET.Element, name: str):
    for candidate in node.iter():
        if _local_name(candidate.tag) == name:
            return candidate
    return None


def _node_text(node) -> str:
    return str(getattr(node, "text", "") or "").strip()


def _response_evidence(response_xml: str) -> dict:
    encoded = str(response_xml or "").encode("utf-8", errors="replace")
    return {
        "response_sha256": hashlib.sha256(encoded).hexdigest(),
        "response_bytes": len(encoded),
    }


class ArcaWsaaSessionMixin:
    """TRA signing, WSAA parsing and service-scoped Ticket coordination.

    Subclasses must provide ``service_name``, credential paths,
    ``ticket_coordinator`` and a strict ``_soap_post`` implementation.
    """

    def _build_tra(self) -> str:
        now_utc = datetime.now(timezone.utc)
        generation = now_utc - timedelta(minutes=5)
        expiration = now_utc + timedelta(minutes=10)
        # WSAA defines uniqueId as xsd:unsignedInt (32 bits).  Values outside
        # this range are rejected before the signed TRA can be authenticated.
        unique_id = secrets.randbelow((1 << 32) - 1) + 1
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<loginTicketRequest version="1.0">'
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
                generated_at = generated_at.replace(tzinfo=timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            generated_at = generated_at.astimezone(timezone.utc)
            expires_at = expires_at.astimezone(timezone.utc)
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
