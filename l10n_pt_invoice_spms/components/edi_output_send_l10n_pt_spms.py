# Copyright 2026 Dixmit
# @author: Enric Tobella
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import logging
from uuid import uuid4

import requests
from lxml import etree

from odoo import fields

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


WSDL = "https://www.ccf.min-saude.pt/WSExternoBroker/ProxyService/FacturaCRDWS"  # noqa: disable=B950
WSDL_TEST = "https://www.ccf.min-saude.pt/WSExternoBroker/ProxyService/FacturaCRDServiceDEV"  # noqa: disable=B950


class EdiOutputSendL10nPtSpms(Component):
    _name = "edi.output.send.l10n_pt_spms"
    _inherit = "edi.component.send.mixin"
    _usage = "output.send"
    _backend_type = "l10n_pt_spms"
    _action = "send"

    def send(self):
        invoice = self.exchange_record.record
        data = self.exchange_record._get_file_content().encode()
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
            "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText",  # noqa: disable=B950
        )
        password.text = invoice.company_id.spms_password
        body = etree.SubElement(root, f"{{{SOAPENV_NS}}}Body")
        action = etree.SubElement(
            body, f"{{{FACTURA_NS}}}submeterFacturaElectronicaCRD"
        )
        factura = etree.SubElement(action, "factura")
        etree.SubElement(factura, "areaConferencia").text = "3"
        etree.SubElement(
            factura, "codigoPrestador"
        ).text = invoice.company_id.sudo().spms_identifier
        etree.SubElement(factura, "dataFactura").text = fields.Date.to_string(
            invoice.invoice_date
        )
        etree.SubElement(factura, "nif").text = vat
        etree.SubElement(
            factura, "numeroFactura"
        ).text = invoice._get_spms_invoice_number()
        etree.SubElement(factura, "documento").text = base64.b64encode(data).decode(
            "ascii"
        )
        etree.SubElement(factura, "ficheiroComprimido").text = "N"
        xml = etree.tostring(root, encoding="utf-8", xml_declaration=False)
        response = requests.post(
            WSDL,
            data=xml.decode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "submeterFacturaElectronicaCRD",
            },
        )
        response.raise_for_status()
        response_xml = etree.fromstring(response.content)
        aceite = response_xml.xpath("//aceite")
        if not aceite or aceite[0].text != "S":
            raise Exception(
                "Invoice not accepted: %s" % response.content.decode("utf-8")
            )
        return response.content.decode("utf-8")
