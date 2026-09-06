# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io
from unittest import mock

import requests
from zeep.transports import Transport

from odoo.tests.common import SavepointCase
from odoo.tools import mute_logger

from odoo.addons.queue_job.exception import RetryableJobError
from odoo.addons.queue_job.job import Job
from odoo.addons.queue_job.tests.common import trap_jobs

MODULE = "odoo.addons.l10n_pt_invoice_spms_check.models.edi_exchange_record"
CLIENT_PATH = MODULE + ".EdiExchangeRecord._l10n_pt_spms_check_client"
TRANSPORT_PATH = MODULE + ".Transport"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
WSSE_NS = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
SERVICE_NS = "http://facturaElectronica.service.cc.ccf/"

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
        200,
        (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/'
            'soap/envelope/"><soapenv:Body>'
            '<ns2:obterResultadoConferenciaResponse xmlns:ns2="http://'
            'facturaElectronica.service.cc.ccf/"><return>' + inner + "</return>"
            "</ns2:obterResultadoConferenciaResponse>"
            "</soapenv:Body></soapenv:Envelope>"
        ).encode(),
    )


def _empty_response():
    """A check answer without the <return> element, which the WSDL
    allows (minOccurs=0)."""
    return (
        200,
        (
            b'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/'
            b'soap/envelope/"><soapenv:Body>'
            b'<ns2:obterResultadoConferenciaResponse xmlns:ns2="http://'
            b'facturaElectronica.service.cc.ccf/"/>'
            b"</soapenv:Body></soapenv:Envelope>"
        ),
    )


def _fault_response(faultstring):
    """A CCF refusal as the live service sends it: HTTP 500 with a SOAP
    fault whose faultstring starts with the code — e.g. the real
    "301 - Factura Inexistente." (probed 2026-08-03)."""
    return (
        500,
        (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/'
            'soap/envelope/"><soapenv:Body><soapenv:Fault>'
            "<faultcode>soapenv:Server</faultcode>"
            "<faultstring>" + faultstring + "</faultstring>"
            "</soapenv:Fault></soapenv:Body></soapenv:Envelope>"
        ).encode(),
    )


class _FakeTransport(Transport):
    """The wire, canned. The module builds its real zeep client from
    the shipped WSDL and posts its envelope here; back comes the
    status code and body the live service would send. The WSSE
    header, the request shape, the base64 decoding and the fault
    handling are all zeep's own work, exercised for real."""

    def __init__(self, *answers, side_effect=None):
        super().__init__()
        self.answers = list(answers)
        self.side_effect = side_effect
        self.envelopes = []

    def post_xml(self, address, envelope, headers):
        self.envelopes.append(envelope)
        if self.side_effect is not None:
            raise self.side_effect
        status_code, content = self.answers.pop(0)
        response = requests.Response()
        response.status_code = status_code
        response.raw = io.BytesIO(content)
        response.headers["Content-Type"] = "text/xml"
        return response


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

    def _run_cron(self, transport):
        """Queue the polls as the scheduled action does, then perform them."""
        with mock.patch(TRANSPORT_PATH, return_value=transport), trap_jobs() as trap:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
            trap.perform_enqueued_jobs()

    def _queued_polls(self):
        """How many polls one pass of the scheduled action queues."""
        with trap_jobs() as trap:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
        return trap.jobs_count()

    def _children(self):
        return self.env["edi.exchange.record"].search(
            [
                ("type_id.code", "=", "l10n_pt_spms_check"),
                ("model", "=", "account.move"),
                ("res_id", "=", self.invoice.id),
            ]
        )

    def _results(self):
        return self.env["spms.invoice.check"].search(
            [("move_id", "=", self.invoice.id)]
        )

    def test_request_carries_the_resolution_key(self):
        # the CCF resolves an invoice by (numeroFactura, dataFactura),
        # wrapped in <factura> — the exact shape production submits
        # with, signed with the credentials of the invoice's company
        transport = _FakeTransport(
            _fault_response("302 - Factura ainda não conferida.")
        )
        self._run_cron(transport)
        self.assertEqual(len(transport.envelopes), 1)
        envelope = transport.envelopes[0]
        username = envelope.find(".//{%s}Username" % WSSE_NS)
        self.assertEqual(username.text, "test-user")
        factura = envelope.find(
            "{%s}Body/{%s}obterResultadoConferencia/factura" % (SOAP_NS, SERVICE_NS)
        )
        self.assertIsNotNone(factura)
        self.assertEqual(
            [(child.tag, child.text) for child in factura],
            [
                ("areaConferencia", "3"),
                ("codigoPrestador", "12345678"),
                ("dataFactura", "2026-05-31"),
                ("nif", "999999990"),
                ("numeroFactura", self.invoice._get_spms_invoice_number()),
            ],
        )

    def test_not_checked_yet_leaves_no_trace(self):
        transport = _FakeTransport(
            _fault_response("302 - Factura ainda não conferida.")
        )
        self._run_cron(transport)
        self.assertEqual(len(transport.envelopes), 1)
        self.assertFalse(self._children())
        self.assertFalse(self._results())

    def test_service_illness_retries_the_job(self):
        # the real July-2026 outage answered this exact fault for weeks:
        # retried like a timeout, and once the retries run out the job
        # fails in the queue — nothing is written on the invoice, which
        # is honestly not checked yet
        transport = _FakeTransport(_fault_response("999 - Erro desconhecido."))
        with self.assertRaises(RetryableJobError):
            self._run_cron(transport)
        self.assertFalse(self._children())
        self.assertFalse(self._results())

    def test_timeout_retries_the_job(self):
        # transport trouble is the job's own business: retried, no trace
        transport = _FakeTransport(side_effect=requests.Timeout("no answer"))
        with self.assertRaises(RetryableJobError):
            self._run_cron(transport)
        self.assertFalse(self._children())
        self.assertFalse(self._results())

    def test_malformed_answer_retries_the_job(self):
        # an outage page instead of SOAP is transport trouble too,
        # whether it is not XML at all or an error page without any
        # CCF verdict in it
        for answer in (
            (200, b"this is not xml"),
            (503, b"<html><body>Service Unavailable</body></html>"),
            (500, b""),
        ):
            with self.subTest(answer=answer):
                transport = _FakeTransport(answer)
                with self.assertRaises(RetryableJobError):
                    self._run_cron(transport)
                self.assertFalse(self._children())
                self.assertFalse(self._results())

    def test_answer_off_the_contract_retries_the_job(self):
        # an answer zeep cannot map on the WSDL is a change of contract,
        # not a quiet "no result yet": retried like a timeout, visible
        # in the queue once the retries run out
        transport = _FakeTransport(_result_response("<somethingElse>x</somethingElse>"))
        with self.assertRaises(RetryableJobError):
            self._run_cron(transport)
        self.assertFalse(self._children())
        self.assertFalse(self._results())

    def test_unrecognised_answer_warns_and_leaves_no_trace(self):
        # an answer the WSDL allows but that carries neither a document
        # nor a fault code must never pass for a quiet "no result yet"
        for answer in (
            _result_response("<numeroFactura>FT TEST/00001</numeroFactura>"),
            _empty_response(),
        ):
            with self.subTest(answer=answer):
                transport = _FakeTransport(answer)
                with self.assertLogs(MODULE, level="WARNING") as capture:
                    self._run_cron(transport)
                self.assertTrue(
                    any("unrecognised answer" in line for line in capture.output)
                )
                self.assertFalse(self._children())
                self.assertFalse(self._results())

    def test_unknown_invoice_flags_incident(self):
        transport = _FakeTransport(_fault_response("301 - Factura Inexistente."))
        self._run_cron(transport)
        self.assertFalse(self._children())
        result = self._results()
        self.assertEqual(len(result), 1)
        self.assertEqual(result.state, "error")
        self.assertEqual(result.ws_incident_code, "301")
        self.assertIn("301", result.error_message)
        self.assertIn(self.invoice.name, result.error_message)
        transport = _FakeTransport(_fault_response("301 - Factura Inexistente."))
        self._run_cron(transport)
        self.assertEqual(len(self._results()), 1)

    def test_definitive_result_supersedes_incident(self):
        transport = _FakeTransport(_fault_response("301 - Factura Inexistente."))
        self._run_cron(transport)
        result = self._results()
        self.assertEqual(result.state, "error")
        self.assertTrue(result.error_message)
        result.write({"check_state": "without_errors"})
        self.assertEqual(result.state, "zero_official")
        self.assertFalse(result.error_message)

    @mute_logger(MODULE)
    def test_document_creates_child_input(self):
        document = base64.b64encode(DOCUMENT_XML.encode()).decode()
        transport = _FakeTransport(
            _result_response("<documento>%s</documento>" % document)
        )
        self._run_cron(transport)
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
        transport = _FakeTransport()
        self._run_cron(transport)
        self.assertFalse(transport.envelopes)
        self.assertEqual(len(self._children()), 1)

    @mute_logger(MODULE)
    def test_wrapped_base64_document_is_decoded(self):
        # the live service line-wraps the base64 payload (XSD
        # base64Binary allows whitespace): a real answer probed on
        # 2026-08-06 carries 3016 newlines inside <documento>
        encoded = base64.b64encode(DOCUMENT_XML.encode()).decode()
        wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
        transport = _FakeTransport(
            _result_response("<documento>%s</documento>" % wrapped)
        )
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(child._get_file_content(), DOCUMENT_XML)

    @mute_logger(MODULE)
    def test_latin1_document_stored_verbatim(self):
        document = base64.b64encode(LATIN1_XML).decode()
        transport = _FakeTransport(
            _result_response("<documento>%s</documento>" % document)
        )
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(base64.b64decode(child.exchange_file), LATIN1_XML)

    def test_definitive_result_not_polled(self):
        self.env["spms.invoice.check"].create(
            {"move_id": self.invoice.id, "check_state": "without_errors"}
        )
        self.assertEqual(self._queued_polls(), 0)

    def test_cancelled_invoice_not_polled(self):
        self.invoice.button_cancel()
        self.assertEqual(self._queued_polls(), 0)

    def test_cron_queues_one_poll_per_invoice(self):
        # the scheduled action never calls the CCF itself: one job per
        # pending invoice, not repeated while the previous one is pending
        with mock.patch(CLIENT_PATH) as factory:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
        factory.assert_not_called()
        jobs = self.env["queue.job"].search(
            [("method_name", "=", "action_l10n_pt_spms_check_poll")]
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs.record_ids, [self.exchange.id])
        self.assertTrue(jobs.identity_key)

    def _fail_queued_poll(self):
        """Queue one real poll job and fail it the way queue_job does
        when its retries run out."""
        with mock.patch(CLIENT_PATH) as factory:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
        factory.assert_not_called()
        record = self.env["queue.job"].search(
            [("method_name", "=", "action_l10n_pt_spms_check_poll")]
        )
        self.assertEqual(len(record), 1)
        job = Job.load(self.env, record.uuid)
        job.retry = 5
        job.set_failed(exc_info="Max. retries (5) reached")
        job.store()
        self.assertEqual(record.state, "failed")
        return record

    def test_failed_poll_is_requeued_not_duplicated(self):
        # a poll that ran out of retries is final for queue_job: the
        # next pass re-activates that job instead of queueing a second
        record = self._fail_queued_poll()
        with mock.patch(CLIENT_PATH) as factory:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
        factory.assert_not_called()
        jobs = self.env["queue.job"].search(
            [("method_name", "=", "action_l10n_pt_spms_check_poll")]
        )
        self.assertEqual(jobs, record)
        self.assertEqual(record.state, "pending")
        self.assertEqual(record.retry, 0)

    def test_failed_poll_of_settled_invoice_left_alone(self):
        # the failed job of an invoice checked meanwhile is not revived
        record = self._fail_queued_poll()
        self.env["spms.invoice.check"].create(
            {"move_id": self.invoice.id, "check_state": "without_errors"}
        )
        self.assertEqual(self._queued_polls(), 0)
        self.assertEqual(record.state, "failed")

    def test_job_skips_invoice_settled_meanwhile(self):
        # a result may land between queueing and running: the job must
        # not ask the CCF again
        self.env["spms.invoice.check"].create(
            {"move_id": self.invoice.id, "check_state": "without_errors"}
        )
        with mock.patch(CLIENT_PATH) as factory:
            self.exchange.action_l10n_pt_spms_check_poll()
        factory.assert_not_called()

    def test_credit_note_not_polled(self):
        """A sent credit note travels through the same exchange type, but
        the CCF checks invoices only: a nota is never polled."""
        self.env["spms.invoice.check"].create(
            {"move_id": self.invoice.id, "check_state": "without_errors"}
        )
        credit_note = self.env["account.move"].create(
            {
                "name": "NC TEST/00001",
                "move_type": "out_refund",
                "partner_id": self.partner.id,
                "journal_id": self.journal.id,
                "invoice_date": "2026-06-30",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Test service",
                            "quantity": 1,
                            "price_unit": 10.0,
                            "account_id": self.income_account.id,
                            "tax_ids": [(5, 0, 0)],
                        },
                    )
                ],
            }
        )
        credit_note.action_post()
        self.backend.create_record(
            "l10n_pt_spms",
            {
                "edi_exchange_state": "output_sent_and_processed",
                "model": "account.move",
                "res_id": credit_note.id,
            },
        )
        self.assertEqual(self._queued_polls(), 0)

    def test_output_sent_also_polled(self):
        self.exchange.edi_exchange_state = "output_sent"
        transport = _FakeTransport(
            _fault_response("302 - Factura ainda não conferida.")
        )
        self._run_cron(transport)
        self.assertEqual(len(transport.envelopes), 1)

    def test_cron_ships_active(self):
        # standard polling pattern: always on, the empty work queue is the gate
        cron = self.env.ref("l10n_pt_invoice_spms_check.cron_spms_check_update")
        self.assertTrue(cron.active)
        self.assertIn("_cron_l10n_pt_spms_check_update", cron.code)
