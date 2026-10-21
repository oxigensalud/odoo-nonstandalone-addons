# Copyright 2026 Dixmit
# @author: Enric Tobella
# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import logging
from http.client import responses as HTTP_REASONS
from uuid import uuid4

import requests
from lxml import etree

from odoo import _, fields
from odoo.exceptions import UserError

from odoo.addons.component.core import Component

_logger = logging.getLogger(__name__)

WSU_NS = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
)
WSSE_NS = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
SOAPENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
FACTURA_NS = "http://facturaElectronica.service.cc.ccf/"
UBL_NAMESPACES = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
APPLICATION_RESPONSE_TAG = (
    "{urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2}"
    "ApplicationResponse"
)


WSDL = "https://www.ccf.min-saude.pt/WSExternoBroker/ProxyService/FacturaCRDWS"
WSDL_TEST = (
    "https://www.ccf.min-saude.pt/WSExternoBroker/ProxyService/FacturaCRDServiceDEV"
)


class EdiOutputSendL10nPtSpms(Component):
    _name = "edi.output.send.l10n_pt_spms"
    _inherit = "edi.component.send.mixin"
    _usage = "output.send"
    _backend_type = "l10n_pt_spms"
    _action = "send"

    def send(self):
        invoice = self.exchange_record.record
        name = invoice._get_move_display_name()
        if invoice.state != "posted":
            raise UserError(
                _("%(name)s is not posted, so it cannot be sent to SPMS.")
                % {"name": name}
            )
        document = self.exchange_record._get_file_content().encode()
        envelope = self._prepare_envelope(invoice, document)
        response = requests.post(
            WSDL,
            data=envelope.decode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "submeterFacturaElectronicaCRD",
            },
        )
        answer = self._spms_parse(response.content)
        if response.status_code >= 400:
            # SPMS answers most errors with our own request, credentials included
            reason = HTTP_REASONS.get(response.status_code)
            status = (
                "HTTP %s %s" % (response.status_code, reason)
                if reason is not None
                else "HTTP %s" % response.status_code
            )
            message = _(
                "SPMS answered with an error (%(status)s); "
                "sending %(name)s will be retried automatically."
            ) % {"status": status, "name": name}
            fault = self._spms_fault_text(answer)
            if fault is not None:
                message += " " + _("SPMS message: %(fault)s") % {"fault": fault}
            raise requests.HTTPError(
                self._spms_masked(message, invoice.company_id), response=response
            )
        verdict = (
            self._spms_single_text(answer.xpath("//aceite"))
            if answer is not None
            else None
        )
        if verdict == "S":
            return response.content.decode("utf-8")
        elif verdict == "N":
            message = "\n".join(
                [_("SPMS did not accept %(name)s.") % {"name": name}]
                + self._spms_refusal_reasons(answer)
            )
            raise UserError(self._spms_masked(message, invoice.company_id))
        else:
            raise UserError(
                _(
                    "The answer from SPMS could not be read, so it is not known "
                    "whether %(name)s was accepted."
                )
                % {"name": name}
            )

    def _spms_parse(self, content):
        """The root element of an XML content, or None when it is not XML."""
        try:
            # no entity is resolved: an answer cannot bring a local file in
            root = etree.fromstring(
                content, parser=etree.XMLParser(resolve_entities=False, no_network=True)
            )
        except etree.XMLSyntaxError:
            # None, so that the callers raise outside this except block
            root = None
        return root

    def _spms_fault_text(self, answer):
        """The text of the SOAP 1.1 faults of the parsed answer, or None."""
        faultstrings = (
            answer.xpath(
                "/soapenv:Envelope/soapenv:Body/soapenv:Fault/faultstring",
                namespaces={"soapenv": SOAPENV_NS},
            )
            if answer is not None
            else []
        )
        return self._spms_joined_text(faultstrings)

    def _spms_refusal_reasons(self, answer):
        """SPMS's reasons for a refusal, one line each, from its documento."""
        encoded = self._spms_single_text(answer.xpath("//documento"))
        try:
            content = (
                base64.b64decode("".join(encoded.split()), validate=True)
                if encoded is not None
                else None
            )
        except ValueError:
            content = None
        reply = self._spms_parse(content) if content is not None else None
        responses = (
            reply.xpath(
                "cac:DocumentResponse/cac:Response"
                " | cac:DocumentResponse/cac:LineResponse/cac:Response",
                namespaces=UBL_NAMESPACES,
            )
            if reply is not None and reply.tag == APPLICATION_RESPONSE_TAG
            else []
        )
        texts = [self._spms_response_text(response) for response in responses]
        return [text for text in texts if text is not None]

    def _spms_response_text(self, response):
        """The text of a response: code and description, the code alone, or None."""
        code = self._spms_single_text(
            response.xpath("cbc:ResponseCode", namespaces=UBL_NAMESPACES)
        )
        description = self._spms_joined_text(
            response.xpath("cbc:Description", namespaces=UBL_NAMESPACES)
        )
        if code is not None:
            text = (
                "%(code)s: %(description)s" % {"code": code, "description": description}
                if description is not None
                else code
            )
        else:
            text = None
        return text

    def _spms_joined_text(self, elements):
        """The readable texts of the elements, joined, or None."""
        texts = [self._spms_text(element) for element in elements]
        kept = [text for text in texts if text is not None]
        return "; ".join(kept) if kept else None

    def _spms_single_text(self, elements):
        """The text of the only element, or None for none or several."""
        return self._spms_text(elements[0]) if len(elements) == 1 else None

    def _spms_text(self, element):
        """The stripped text of an element without children, or None."""
        if len(element) == 0:
            text = (element.text or "").strip() or None
        else:
            text = None
        return text

    def _spms_masked(self, text, company):
        """The text with our SPMS username and password replaced by ***."""
        secrets = [
            secret
            for secret in (company.spms_username, company.spms_password)
            if secret
        ]
        # the longer first, so that neither leaves part of the other visible
        for secret in sorted(secrets, key=len, reverse=True):
            text = text.replace(secret, "***")
        return text

    def _prepare_envelope(self, invoice, document):
        vat = invoice.company_id.vat
        if vat and len(vat) >= 11:
            vat = vat[-9:]
        root = etree.Element(
            f"{{{SOAPENV_NS}}}Envelope",
            nsmap={"soapenv": SOAPENV_NS, "fac": FACTURA_NS},
        )
        header = etree.SubElement(root, f"{{{SOAPENV_NS}}}Header")
        security = etree.SubElement(
            header, f"{{{WSSE_NS}}}Security", nsmap={"wsse": WSSE_NS, "wsu": WSU_NS}
        )
        security.set(etree.QName(SOAPENV_NS, "mustUnderstand"), "1")
        username_token = etree.SubElement(security, f"{{{WSSE_NS}}}UsernameToken")
        username_token.set(
            etree.QName(WSU_NS, "Id"),
            f"UsernameToken-{uuid4()}",
        )
        username = etree.SubElement(username_token, f"{{{WSSE_NS}}}Username")
        username.text = invoice.company_id.spms_username
        password = etree.SubElement(username_token, f"{{{WSSE_NS}}}Password")
        password.set(
            "Type",
            "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText",  # noqa: B950
        )
        password.text = invoice.company_id.spms_password
        body = etree.SubElement(root, f"{{{SOAPENV_NS}}}Body")
        note_type = invoice._spms_note_type()
        if note_type is None:
            action = etree.SubElement(
                body, f"{{{FACTURA_NS}}}submeterFacturaElectronicaCRD"
            )
            factura = etree.SubElement(action, "factura")
        else:
            action = etree.SubElement(body, f"{{{FACTURA_NS}}}submeterNotaCredDebCRD")
            factura = etree.SubElement(action, "nota")

        origin_invoice = invoice._spms_origin_invoice() or invoice
        etree.SubElement(factura, "areaConferencia").text = "3"
        etree.SubElement(
            factura, "codigoPrestador"
        ).text = invoice.partner_id.sudo().spms_assigned_id
        etree.SubElement(factura, "dataFactura").text = fields.Date.to_string(
            origin_invoice.invoice_date
        )
        etree.SubElement(factura, "nif").text = vat
        etree.SubElement(
            factura, "numeroFactura"
        ).text = origin_invoice._get_spms_invoice_number()
        if note_type is None:
            etree.SubElement(factura, "ficheiroComprimido").text = "N"
        else:
            etree.SubElement(factura, "tipoNota").text = note_type
            etree.SubElement(
                factura, "numeroNota"
            ).text = invoice._get_spms_invoice_number()
        etree.SubElement(factura, "documento").text = base64.b64encode(document).decode(
            "ascii"
        )
        return etree.tostring(root, encoding="utf-8", xml_declaration=False)
