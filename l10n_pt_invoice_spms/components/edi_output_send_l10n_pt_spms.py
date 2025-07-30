# Copyright 2020 Creu Blanca
# @author: Enric Tobella
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import logging

from zeep import Client

from odoo import fields

from odoo.addons.component.core import Component

_logger = logging.getLogger(__name__)


WSDL = "https://www.spms.min-saude.pt/wp-content/uploads/2020/01/Facturacao-CRD-2.11.wsdl"  # noqa: disable=B950


class EdiOutputSendL10nPtSpms(Component):
    _name = "edi.output.send.l10n_pt_spms"
    _inherit = "edi.component.send.mixin"
    _usage = "output.send"
    _backend_type = "l10n_pt_spms"
    _action = "send"

    def send(self):
        client = Client(WSDL)  # TODO: Pass the WSDL URL to a configuration parameter
        invoice = self.exchange_record.record
        request = client.get_type("ns0:submeterFacturaElectronicaCRD")(
            "3",
            invoice.company_id.sudo().spms_identifier,
            fields.Date.to_string(invoice.invoice_date),
            invoice.company_id.vat,
            invoice.name,
            base64.b64encode(self.exchange_record._get_file_content().encode()).decode(
                "utf-8"
            ),
            "N",
        )
        response = client.service.submeterFacturaElectronicaCRD(request)
        _logger.info(response)
        raise Exception("Not implemented")
