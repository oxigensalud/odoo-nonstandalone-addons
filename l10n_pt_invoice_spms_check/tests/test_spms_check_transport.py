# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io
from unittest import mock

import requests
from zeep.transports import Transport

from odoo.addons.component.tests.common import SavepointComponentCase
from odoo.addons.queue_job.job import Job
from odoo.addons.queue_job.tests.common import trap_jobs

MODULE = "odoo.addons.l10n_pt_invoice_spms_check.models.edi_exchange_record"
CLIENT_PATH = MODULE + ".EdiExchangeRecord._l10n_pt_spms_check_client"
FETCH_PATH = MODULE + ".EdiExchangeRecord._l10n_pt_spms_check_fetch"
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
NOT_CHECKED_YET = "302 - Factura ainda não conferida."


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


def _document_response(payload):
    """A check answer carrying the conference document, base64 as the
    service encodes it."""
    return _result_response(
        "<documento>%s</documento>" % base64.b64encode(payload).decode()
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


class TestSpmsCheckTransport(SavepointComponentCase):
    """Exercise the polling cron against a mocked CCF web service.

    SavepointComponentCase builds the components registry itself: a
    received document is processed by the parser component right away,
    so the class must not depend on another test class having loaded
    the components before it."""

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

    def _settle(self):
        """A definitive check result on the invoice, out of band."""
        return self.env["spms.invoice.check"].create(
            {"move_id": self.invoice.id, "check_state": "without_errors"}
        )

    def test_request_carries_the_resolution_key(self):
        # the CCF resolves an invoice by (numeroFactura, dataFactura),
        # wrapped in <factura> — the exact shape production submits
        # with, signed with the credentials of the invoice's company
        transport = _FakeTransport(_fault_response(NOT_CHECKED_YET))
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

    def test_result_record_expected_from_the_first_pass(self):
        # the first pass after the sending creates the result record,
        # waiting and empty, as the ACK the sent invoice expects: the
        # sent record knows what it is waiting for from the start
        transport = _FakeTransport(_fault_response(NOT_CHECKED_YET))
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(child.edi_exchange_state, "input_pending")
        self.assertEqual(child.parent_id, self.exchange)
        self.assertFalse(child.exchange_file)
        self.assertFalse(child.exchange_error)
        self.assertEqual(self.exchange.ack_exchange_id, child)
        self.assertTrue(self.exchange.ack_expected)
        self.assertFalse(self._results())
        self.assertEqual(len(transport.envelopes), 1)

    def test_not_checked_yet_keeps_waiting(self):
        # "not checked yet" is the normal answer for weeks: the record
        # keeps waiting, asked again at every pass, nothing else written
        transport = _FakeTransport(
            _fault_response(NOT_CHECKED_YET), _fault_response(NOT_CHECKED_YET)
        )
        self._run_cron(transport)
        self._run_cron(transport)
        self.assertEqual(len(transport.envelopes), 2)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(child.edi_exchange_state, "input_pending")
        self.assertFalse(child.exchange_file)
        self.assertFalse(child.exchange_error)
        self.assertFalse(self._results())

    def test_service_illness_is_an_error_on_reception(self):
        # the real July-2026 outage answered this exact fault for weeks:
        # the record says so, with the reason, and is asked again at the
        # next pass — the job itself stays green; the next "not checked
        # yet" clears the error by itself
        transport = _FakeTransport(
            _fault_response("999 - Erro desconhecido."),
            _fault_response(NOT_CHECKED_YET),
        )
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(child.edi_exchange_state, "input_receive_error")
        self.assertIn("999", child.exchange_error)
        self.assertFalse(child.exchange_error_traceback)
        self.assertFalse(self._results())
        self._run_cron(transport)
        self.assertEqual(len(transport.envelopes), 2)
        self.assertEqual(child.edi_exchange_state, "input_pending")
        self.assertFalse(child.exchange_error)
        self.assertFalse(child.exchange_error_traceback)

    def test_timeout_is_an_error_on_reception(self):
        # transport trouble is written on the record like any other
        # answer, the only one that keeps its traceback
        transport = _FakeTransport(side_effect=requests.Timeout("no answer"))
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(child.edi_exchange_state, "input_receive_error")
        self.assertIn("could not be reached", child.exchange_error)
        self.assertTrue(child.exchange_error_traceback)
        self.assertFalse(self._results())

    def test_malformed_answer_is_an_error_on_reception(self):
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
                self._run_cron(transport)
                child = self._children()
                self.assertEqual(child.edi_exchange_state, "input_receive_error")
                self.assertIn("could not be reached", child.exchange_error)
                self.assertFalse(self._results())

    def test_answer_off_the_contract_is_an_error_on_reception(self):
        # an answer zeep cannot map on the WSDL is a change of contract,
        # not a quiet "no result yet"
        transport = _FakeTransport(_result_response("<somethingElse>x</somethingElse>"))
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(child.edi_exchange_state, "input_receive_error")
        self.assertIn("could not be reached", child.exchange_error)
        self.assertTrue(child.exchange_error_traceback)
        self.assertFalse(self._results())

    def test_answer_without_document_is_an_error_on_reception(self):
        # an answer the WSDL allows but that carries neither a document
        # nor a fault code must never pass for a quiet "no result yet"
        for answer in (
            _result_response("<numeroFactura>FT TEST/00001</numeroFactura>"),
            _empty_response(),
        ):
            with self.subTest(answer=answer):
                transport = _FakeTransport(answer)
                self._run_cron(transport)
                child = self._children()
                self.assertEqual(child.edi_exchange_state, "input_receive_error")
                self.assertIn("without a check document", child.exchange_error)
                self.assertFalse(child.exchange_error_traceback)
                self.assertFalse(self._results())

    def test_unknown_invoice_is_an_error_on_reception(self):
        # "factura inexistente" on an invoice sent successfully is a
        # condition of the CCF written where the user looks, and the
        # invoice keeps being asked for until the CCF answers
        transport = _FakeTransport(
            _fault_response("301 - Factura Inexistente."),
            _document_response(DOCUMENT_XML.encode()),
        )
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(child.edi_exchange_state, "input_receive_error")
        self.assertIn("301", child.exchange_error)
        self.assertIn(self.invoice.name, child.exchange_error)
        self.assertFalse(child.exchange_error_traceback)
        self.assertFalse(self._results())
        self._run_cron(transport)
        self.assertEqual(len(transport.envelopes), 2)
        self.assertEqual(self._children(), child)
        self.assertEqual(child._get_file_content(), DOCUMENT_XML)
        self.assertIn(
            child.edi_exchange_state, ["input_processed", "input_processed_error"]
        )

    def test_unexpected_return_code_is_an_error_on_reception(self):
        # a return code outside the known ones is not silently ignored
        transport = _FakeTransport(_fault_response("303 - Sem resultado."))
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(child.edi_exchange_state, "input_receive_error")
        self.assertIn("303", child.exchange_error)
        self.assertFalse(child.exchange_error_traceback)
        self.assertFalse(self._results())

    def test_document_received_and_processed(self):
        transport = _FakeTransport(_document_response(DOCUMENT_XML.encode()))
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
        # the reception is the arrival of the ACK the sent invoice expects
        self.assertTrue(child.exchanged_on)
        self.assertEqual(self.exchange.ack_received_on, child.exchanged_on)
        # one message on the invoice for the reception itself
        received = self.invoice.message_ids.filtered(
            lambda message: child.identifier in (message.body or "")
            and "received successfully" in (message.body or "")
        )
        self.assertEqual(len(received), 1)
        # while the fetched document waits for processing, no re-poll
        transport = _FakeTransport()
        self._run_cron(transport)
        self.assertFalse(transport.envelopes)
        self.assertEqual(self._children(), child)

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

    def test_latin1_document_stored_verbatim(self):
        transport = _FakeTransport(_document_response(LATIN1_XML))
        self._run_cron(transport)
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(base64.b64decode(child.exchange_file), LATIN1_XML)

    def test_definitive_result_not_polled(self):
        self._settle()
        self.assertEqual(self._queued_polls(), 0)
        self.assertFalse(self._children())

    def test_cancelled_invoice_not_polled(self):
        self.invoice.button_cancel()
        self.assertEqual(self._queued_polls(), 0)
        self.assertFalse(self._children())

    def test_cron_queues_one_poll_per_invoice(self):
        # the scheduled action never calls the CCF itself: one job per
        # waiting result, not repeated while the previous one is pending
        with mock.patch(CLIENT_PATH) as factory:
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
            self.env["edi.exchange.record"]._cron_l10n_pt_spms_check_update()
        factory.assert_not_called()
        child = self._children()
        self.assertEqual(len(child), 1)
        jobs = self.env["queue.job"].search(
            [("method_name", "=", "action_l10n_pt_spms_check_poll")]
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs.record_ids, [child.id])
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
        # a poll that failed is final for queue_job: the next pass
        # re-activates that job instead of queueing a second
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
        self._settle()
        self.assertEqual(self._queued_polls(), 0)
        self.assertEqual(record.state, "failed")

    def test_job_skips_invoice_settled_meanwhile(self):
        # a result may land between queueing and running: the job must
        # not ask the CCF again
        child = self.exchange.exchange_create_ack_record(
            edi_exchange_state="input_pending"
        )
        self._settle()
        with mock.patch(CLIENT_PATH) as factory:
            child.action_l10n_pt_spms_check_poll()
        factory.assert_not_called()

    def test_check_result_is_the_ack_of_the_sent_invoice(self):
        # edi_oca models an expected answer document as the ACK of the
        # exchange: the sending type of l10n_pt_invoice_spms names our
        # check result type as its ACK type, so a sent invoice knows it
        # is waiting for its result and shows when it arrived
        send_type = self.env.ref("l10n_pt_invoice_spms.spms_exchange_type")
        check_type = self.env.ref("l10n_pt_invoice_spms_check.spms_check_exchange_type")
        self.assertEqual(send_type.ack_type_id, check_type)
        self.assertTrue(self.exchange.ack_expected)
        self.assertTrue(self.exchange.needs_ack())
        self.assertFalse(self.exchange.ack_exchange_id)
        self.assertFalse(self.exchange.ack_received_on)
        child = self.backend.create_record(
            "l10n_pt_spms_check",
            {
                "edi_exchange_state": "input_received",
                "model": "account.move",
                "res_id": self.invoice.id,
                "parent_id": self.exchange.id,
            },
        )
        self.assertEqual(self.exchange.ack_exchange_id, child)
        self.assertFalse(self.exchange.needs_ack())
        self.assertTrue(child.exchanged_on)
        self.assertEqual(self.exchange.ack_received_on, child.exchanged_on)
        # the result itself answers nothing: no ACK expected on it, even
        # when computed in one batch with the sent invoice
        self.env["edi.exchange.record"].invalidate_cache(["ack_expected"])
        records = self.exchange | child
        self.assertEqual(records.mapped("ack_expected"), [True, False])

    def test_credit_note_not_polled(self):
        """A sent credit note travels through the same exchange type, but
        the CCF checks invoices only: a nota is never polled, its sent
        record expects no check result and gets no result record."""
        self._settle()
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
        sent_note = self.backend.create_record(
            "l10n_pt_spms",
            {
                "edi_exchange_state": "output_sent_and_processed",
                "model": "account.move",
                "res_id": credit_note.id,
            },
        )
        self.assertFalse(sent_note.ack_expected)
        self.assertEqual(self._queued_polls(), 0)
        self.assertFalse(sent_note.related_exchange_ids)

    def test_output_sent_also_polled(self):
        self.exchange.edi_exchange_state = "output_sent"
        transport = _FakeTransport(_fault_response(NOT_CHECKED_YET))
        self._run_cron(transport)
        self.assertEqual(len(transport.envelopes), 1)

    def test_generic_input_sync_leaves_the_result_records_alone(self):
        # a waiting result has no receive component: the hourly input
        # sync of edi_oca must not pick it up, the module's own
        # scheduled action asks the CCF instead
        self._run_cron(_FakeTransport(_fault_response(NOT_CHECKED_YET)))
        child = self._children()
        self.assertEqual(child.edi_exchange_state, "input_pending")
        self.assertFalse(child.exchange_file)
        pending = self.env["edi.exchange.record"].search(
            self.backend._input_pending_records_domain()
        )
        self.assertNotIn(child, pending)

    def test_unexpected_failure_fails_the_job(self):
        # a software failure is the only thing that turns a job red,
        # and it leaves the record exactly as it was
        with mock.patch(FETCH_PATH, side_effect=RuntimeError("a bug")):
            with self.assertRaises(RuntimeError):
                self._run_cron(_FakeTransport())
        child = self._children()
        self.assertEqual(len(child), 1)
        self.assertEqual(child.edi_exchange_state, "input_pending")
        self.assertFalse(child.exchange_error)
        self.assertFalse(child.exchange_error_traceback)

    def test_cron_ships_active(self):
        # standard polling pattern: always on, the empty work queue is the gate
        cron = self.env.ref("l10n_pt_invoice_spms_check.cron_spms_check_update")
        self.assertTrue(cron.active)
        self.assertIn("_cron_l10n_pt_spms_check_update", cron.code)
