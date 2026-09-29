# Copyright 2026 Dixmit
# @author: Enric Tobella
# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from zeep import Client
from zeep.exceptions import Error as ZeepError
from zeep.exceptions import Fault, TransportError
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken

from odoo import _
from odoo.exceptions import UserError
from odoo.tools.misc import file_path

from odoo.addons.component.core import Component

REQUEST_TIMEOUT = 60


class SpmsTransport(Transport):
    """zeep's transport, remembering the last answer of the CCF: its HTTP
    status and its body as received. zeep drops both once it has
    interpreted the answer, and they are what an error must show for the
    reader to see what the CCF actually said.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_status = None
        self.last_body = None

    def post(self, address, message, headers):
        response = super().post(address, message, headers)
        self.last_status = response.status_code
        self.last_body = response.text
        return response


class EdiOutputSendL10nPtSpms(Component):
    _name = "edi.output.send.l10n_pt_spms"
    _inherit = "edi.component.send.mixin"
    _usage = "output.send"
    _backend_type = "l10n_pt_spms"
    _action = "send"

    def _client(self, company):
        """One zeep client per sending: the WSSE token carries the portal
        credentials of the invoice's company.

        The shipped WSDL is the live one with its write path fixed (see
        api/FacturaCRDWS.wsdl): the live file declares the credit note
        request without its <nota> wrapper, omits the <return> wrapper
        of the answers, points to an internal host and types the base64
        document as a plain string — zeep against the raw live WSDL
        cannot work.
        """
        wsdl = file_path("l10n_pt_invoice_spms/api/FacturaCRDWS.wsdl")
        return Client(
            wsdl,
            wsse=UsernameToken(company.spms_username, company.spms_password),
            transport=SpmsTransport(
                timeout=REQUEST_TIMEOUT, operation_timeout=REQUEST_TIMEOUT
            ),
        )

    def send(self):
        invoice = self.exchange_record.record
        document = self.exchange_record._get_file_content(as_bytes=True)
        vat = invoice.company_id.vat
        if vat and len(vat) >= 11:
            vat = vat[-9:]
        original = invoice.reversed_entry_id or invoice
        identification = {
            "areaConferencia": 3,
            "codigoPrestador": invoice.partner_id.sudo().spms_assigned_id,
            "dataFactura": original.invoice_date,
            "nif": vat,
            "numeroFactura": original._get_spms_invoice_number(),
        }
        client = self._client(invoice.company_id)
        transport = client.transport
        try:
            if invoice.reversed_entry_id:
                answer = client.service.submeterNotaCredDebCRD(
                    nota={
                        **identification,
                        "tipoNota": "C",
                        "numeroNota": invoice._get_spms_invoice_number(),
                        "documento": document,
                    }
                )
            else:
                # <factura> types the nif as xsd:long; <nota> keeps a string
                answer = client.service.submeterFacturaElectronicaCRD(
                    factura={
                        **identification,
                        "nif": int(vat) if vat and vat.isdigit() else None,
                        "documento": document,
                        "ficheiroComprimido": "N",
                    }
                )
        except (Fault, TransportError) as err:
            # an HTTP error answer: retried by the queue as the HTTPError
            # of the hand-built envelope was (see models/edi_backend.py),
            # with the answer in the error
            err.args = (f"{err.args[0]}\nSPMS response body:\n{transport.last_body}",)
            raise
        except ZeepError as err:
            # HTTP 200, but the answer does not follow the shipped WSDL:
            # neither an acceptance nor a refusal
            raise UserError(
                _(
                    "The CCF answer could not be interpreted (HTTP %(status)s, "
                    "%(detail)s).\nSPMS response body:\n%(body)s"
                )
                % {
                    "status": transport.last_status,
                    "detail": type(err).__name__,
                    "body": transport.last_body,
                }
            ) from err
        if answer is None or answer.aceite != "S":
            raise UserError(_("Invoice not accepted: %s") % transport.last_body)
        return transport.last_body
