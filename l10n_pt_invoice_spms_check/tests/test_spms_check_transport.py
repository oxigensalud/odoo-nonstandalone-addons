# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from datetime import date
from unittest import mock

import requests

from odoo.tests.common import SavepointCase
from odoo.tools import mute_logger

MODULE = "odoo.addons.l10n_pt_invoice_spms_check.models.edi_exchange_record"
CLIENT_PATH = MODULE + ".EdiExchangeRecord._l10n_pt_spms_check_client"

DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<ApplicationResponse><SyntheticCheckResult/></ApplicationResponse>"
)
# declared non-UTF-8 encoding: must be stored byte-for-byte
LATIN1_XML = (
    '<?xml version="1.0" encoding="ISO-8859-1"?>'
    "<ApplicationResponse><Mensagem>Prescrição inválida</Mensagem>"
    "</ApplicationResponse>"
).encode("iso-8859-1")


def _result_response(inner):
    """A check answer as the live service sends it: HTTP 200 with the
    JAX-WS <return> wrapper (probed 2026-08-03)."""
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/'
        'soap/envelope/"><soapenv:Body>'
        '<ns2:obterResultadoConferenciaResponse xmlns:ns2="http://'
        'facturaElectronica.service.cc.ccf/"><return>' + inner + "</return>"
        "</ns2:obterResultadoConferenciaResponse>"
        "</soapenv:Body></soapenv:Envelope>"
    ).encode()


def _fault_response(faultstring):
    """A CCF refusal as the live service sends it: HTTP 500 with a SOAP
    fault whose faultstring starts with the code — e.g. the real
    "301 - Factura Inexistente." (probed 2026-08-03)."""
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/'
        'soap/envelope/"><soapenv:Body><soapenv:Fault>'
        "<faultcode>soapenv:Server</faultcode>"
        "<faultstring>" + faultstring + "</faultstring>"
        "</soapenv:Fault></soapenv:Body></soapenv:Envelope>"
    ).encode()


def _mock_client(*responses, side_effect=None):
    """A fake zeep client: raw_response mode returns requests-like
    responses, so only .content matters."""
    client = mock.Mock()
    if side_effect is not None:
        client.service.obterResultadoConferencia.side_effect = side_effect
    else:
        client.service.obterResultadoConferencia.side_effect = [
            mock.Mock(content=content) for content in responses
        ]
    return client


class TestSpmsCheckTransport(SavepointCase):
    """Exercise the polling cron against a mocked CCF web service."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.vat = "PT999999990"
        cls.company.spms_username = "test-user"
        cls.company.spms_password = "test-secret"
        cls.backend = cls.env.ref("l10n_pt_invoice_spms.spms_backend")
        cls.income_account = cls.env["account.account"].search(
            [
                ("company_id", "=", cls.company.id),
                (
                    "user_type_id",
                    "=",
                    cls.env.ref("account.data_account_type_revenue").id,
                ),
            ],
            limit=1,
        )
        journal_vals = {
            "name": "SPMS Transport Test Sales",
            "code": "TSPT",
            "type": "sale",
            "company_id": cls.company.id,
            "default_account_id": cls.income_account.id,
        }
        if "edi_format_ids" in cls.env["account.journal"]._fields:
            journal_vals["edi_format_ids"] = [(5, 0, 0)]
        cls.journal = cls.env["account.journal"].create(journal_vals)
        cls.partner = cls.env["res.partner"].create(
            {"name": "Transport Partner", "spms_assigned_id": "12345678"}
        )
        cls.invoice = cls.env["account.move"].create(
            {
                "name": "FT TEST/00001",
                "move_type": "out_invoice",
                "partner_id": cls.partner.id,
                "journal_id": cls.journal.id,
                "invoice_date": "2026-05-31",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Test service",
                            "quantity": 1,
                            "price_unit": 100.0,
                            "account_id": cls.income_account.id,
                            "tax_ids": [(5, 0, 0)],
                        },
                    )
                ],
            }
        )
        cls.invoice.action_post()
        cls.exchange = cls.backend.create_record(
            "l10n_pt_spms",
            {
                "edi_exchange_state": "output_sent_and_processed",
                "model": "account.move",
                "res_id": cls.invoice.id,
            },
        )

    def _run_cron(self, client):
        with mock.patch(CLIENT_PATH, return_value=client):
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()

    def _children(self):
        return self.env["edi.exchange.record"].search(
            [
                ("type_id.code", "=", "l10n_pt_spms_check"),
                ("model", "=", "account.move"),
                ("res_id", "=", self.invoice.id),
            ]
        )

    def _checks(self):
        return self.env["spms.invoice.check"].search(
            [("move_id", "=", self.invoice.id)]
        )

    def test_request_carries_the_resolution_key(self):
        # the CCF resolves an invoice by (numeroFactura, dataFactura),
        # wrapped in <factura> — the exact shape production submits with
        client = _mock_client(_fault_response("302 - Factura ainda não conferida."))
        self._run_cron(client)
        client.service.obterResultadoConferencia.assert_called_once()
        args, kwargs = client.service.obterResultadoConferencia.call_args
        self.assertFalse(args)
        factura = kwargs["factura"]
        self.assertEqual(factura["areaConferencia"], 3)
        self.assertEqual(factura["codigoPrestador"], "12345678")
        self.assertEqual(factura["dataFactura"], date(2026, 5, 31))
        self.assertEqual(factura["nif"], 999999990)
        self.assertEqual(
            factura["numeroFactura"], self.invoice._get_spms_invoice_number()
        )

    def test_not_checked_yet_leaves_no_trace(self):
        client = _mock_client(_fault_response("302 - Factura ainda não conferida."))
        self._run_cron(client)
        client.service.obterResultadoConferencia.assert_called_once()
        self.assertFalse(self._children())
        self.assertFalse(self._checks())

    def test_service_illness_leaves_no_trace(self):
        # the real July-2026 outage answered this exact fault for weeks
        client = _mock_client(_fault_response("999 - Erro desconhecido."))
        self._run_cron(client)
        self.assertFalse(self._children())
        self.assertFalse(self._checks())

    def test_timeout_leaves_no_trace(self):
        client = _mock_client(side_effect=requests.Timeout("no answer"))
        with self.assertLogs(MODULE, level="WARNING") as capture:
            self._run_cron(client)
        self.assertTrue(any("request failed" in line for line in capture.output))
        self.assertFalse(self._children())
        self.assertFalse(self._checks())

    def test_malformed_answer_warns_and_leaves_no_trace(self):
        client = _mock_client(b"this is not xml")
        with self.assertLogs(MODULE, level="WARNING") as capture:
            self._run_cron(client)
        self.assertTrue(any("request failed" in line for line in capture.output))
        self.assertFalse(self._children())
        self.assertFalse(self._checks())

    def test_unrecognised_answer_warns_and_leaves_no_trace(self):
        # an answer with neither documento, fault code nor return code
        # must never pass for a quiet "no result yet"
        client = _mock_client(_result_response("<somethingElse>x</somethingElse>"))
        with self.assertLogs(MODULE, level="WARNING") as capture:
            self._run_cron(client)
        self.assertTrue(any("unrecognised answer" in line for line in capture.output))
        self.assertFalse(self._children())
        self.assertFalse(self._checks())

    def test_unknown_invoice_flags_incident(self):
        client = _mock_client(_fault_response("301 - Factura Inexistente."))
        self._run_cron(client)
        self.assertFalse(self._children())
        check = self._checks()
        self.assertEqual(len(check), 1)
        self.assertEqual(check.state, "error")
        self.assertEqual(check.ws_incident_code, "301")
        self.assertIn("301", check.error_message)
        self.assertIn(self.invoice.name, check.error_message)
        client = _mock_client(_fault_response("301 - Factura Inexistente."))
        self._run_cron(client)
        self.assertEqual(len(self._checks()), 1)

    def test_definitive_result_supersedes_incident(self):
        client = _mock_client(_fault_response("301 - Factura Inexistente."))
        self._run_cron(client)
        check = self._checks()
        self.assertEqual(check.state, "error")
        self.assertTrue(check.error_message)
        check.write({"check_state": "without_errors"})
        self.assertEqual(check.state, "zero_official")
        self.assertFalse(check.error_message)

    @mute_logger(MODULE)
    def test_document_creates_child_input(self):
        document = base64.b64encode(DOCUMENT_XML.encode()).decode()
        client = _mock_client(_result_response("<documento>%s</documento>" % document))
        self._run_cron(client)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(child.parent_id, self.exchange)
        self.assertEqual(child._get_file_content(), DOCUMENT_XML)
        # whatever the processing outcome, the payload must stay stored
        self.assertIn(
            child.edi_exchange_state,
            ["input_received", "input_processed", "input_processed_error"],
        )
        # while the fetched document waits for processing, no re-poll
        client = _mock_client()
        self._run_cron(client)
        client.service.obterResultadoConferencia.assert_not_called()
        self.assertEqual(len(self._children()), 1)

    @mute_logger(MODULE)
    def test_inline_document_stored(self):
        inline = DOCUMENT_XML.replace("&", "&amp;").replace("<", "&lt;")
        client = _mock_client(_result_response("<documento>%s</documento>" % inline))
        self._run_cron(client)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(child._get_file_content(), DOCUMENT_XML)

    @mute_logger(MODULE)
    def test_latin1_document_stored_verbatim(self):
        document = base64.b64encode(LATIN1_XML).decode()
        client = _mock_client(_result_response("<documento>%s</documento>" % document))
        self._run_cron(client)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(base64.b64decode(child.exchange_file), LATIN1_XML)

    def test_definitive_result_not_polled(self):
        self.env["spms.invoice.check"].create(
            {"move_id": self.invoice.id, "check_state": "without_errors"}
        )
        with mock.patch(CLIENT_PATH) as factory:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
        factory.assert_not_called()

    def test_cancelled_invoice_not_polled(self):
        self.invoice.button_cancel()
        with mock.patch(CLIENT_PATH) as factory:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
        factory.assert_not_called()

    def test_output_sent_also_polled(self):
        self.exchange.edi_exchange_state = "output_sent"
        client = _mock_client(_fault_response("302 - Factura ainda não conferida."))
        self._run_cron(client)
        client.service.obterResultadoConferencia.assert_called_once()

    def test_cron_ships_active(self):
        # standard polling pattern: always on, the empty work queue is the gate
        cron = self.env.ref("l10n_pt_invoice_spms_check.spms_check_update_cron")
        self.assertTrue(cron.active)
        self.assertIn("_cron_l10n_pt_spms_check_update", cron.code)
