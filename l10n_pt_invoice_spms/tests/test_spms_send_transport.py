# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io
from datetime import date
from unittest import mock

import requests

from odoo.addons.component.tests.common import ComponentMixin
from odoo.addons.l10n_pt_invoice_spms.components.edi_output_send_l10n_pt_spms import (
    SpmsTransport,
)
from odoo.addons.queue_job.exception import RetryableJobError

from .common import SpmsInvoiceCase

MODULE = "odoo.addons.l10n_pt_invoice_spms.components.edi_output_send_l10n_pt_spms"
TRANSPORT_PATH = MODULE + ".SpmsTransport"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
WSSE_NS = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
SERVICE_NS = "http://facturaElectronica.service.cc.ccf/"
DOCUMENT = b'<?xml version="1.0" encoding="UTF-8"?><Invoice>signed</Invoice>'


def _envelope(body):
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/'
        'soap/envelope/"><soapenv:Body>' + body + "</soapenv:Body></soapenv:Envelope>"
    ).encode()


def _invoice_answer(aceite):
    """An invoice submission answer over HTTP 200 with the JAX-WS <return>
    wrapper the shipped WSDL declares (see api/FacturaCRDWS.wsdl)."""
    return (
        200,
        _envelope(
            f'<ns2:submeterFacturaElectronicaCRDResponse xmlns:ns2="{SERVICE_NS}">'
            f"<return><areaConferencia>3</areaConferencia><aceite>{aceite}</aceite>"
            "<documento>ok</documento></return>"
            "</ns2:submeterFacturaElectronicaCRDResponse>"
        ),
    )


def _note_answer(aceite):
    """A credit note submission answer, verified against production."""
    return (
        200,
        _envelope(
            f'<ns2:submeterNotaCredDebCRDResponse xmlns:ns2="{SERVICE_NS}">'
            f"<return><tipoNota>C</tipoNota><aceite>{aceite}</aceite></return>"
            "</ns2:submeterNotaCredDebCRDResponse>"
        ),
    )


def _flat_invoice_answer(aceite):
    """An invoice submission answer shaped as the live WSDL declares it,
    without the <return> wrapper: off the shipped contract."""
    return (
        200,
        _envelope(
            f'<ns2:submeterFacturaElectronicaCRDResponse xmlns:ns2="{SERVICE_NS}">'
            f"<areaConferencia>3</areaConferencia><aceite>{aceite}</aceite>"
            "</ns2:submeterFacturaElectronicaCRDResponse>"
        ),
    )


def _fault_answer(faultstring):
    """A CCF refusal as the live service sends it: HTTP 500 with a SOAP
    fault."""
    return (
        500,
        _envelope(
            "<soapenv:Fault><faultcode>soapenv:Server</faultcode>"
            f"<faultstring>{faultstring}</faultstring></soapenv:Fault>"
        ),
    )


def _empty_body_answer():
    """HTTP 500 with a well-formed envelope and an empty Body, as the live
    CCF sends when the broker gets nothing from its backend (observed
    2026-09-09)."""
    return (500, _envelope(""))


class _FakeTransport(SpmsTransport):
    """The wire, canned. The module builds its real zeep client from the
    shipped WSDL and posts its envelope here; back comes the status code
    and body the live service would send. The WSSE header, the request
    shape, the base64 encoding and the fault handling are all zeep's own
    work, exercised for real."""

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
        response.reason = "Internal Server Error" if status_code == 500 else "OK"
        response.raw = io.BytesIO(content)
        response.headers["Content-Type"] = "text/xml"
        self.last_status = response.status_code
        self.last_body = content.decode("utf-8", "replace")
        return response


class TestSpmsSendTransport(SpmsInvoiceCase, ComponentMixin):
    """Exercise the sending component against a mocked CCF web service."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.setUpComponent()
        cls.company.vat = "PT999999990"
        cls.company.spms_username = "test-user"
        cls.company.spms_password = "test-secret"
        cls.partner.spms_assigned_id = "12345678"

    def setUp(self):
        super().setUp()
        self.setUpComponentRegistryReady()

    def _pending(self, move):
        record = self._sending_record(move, "output_pending")
        record._set_file_content(DOCUMENT)
        return record

    def _send(self, record, transport):
        with mock.patch(TRANSPORT_PATH, return_value=transport):
            return self.backend.exchange_send(record)

    def _posted_invoice(self):
        invoice = self._create_invoice()
        invoice.action_post()
        return invoice

    def _posted_credit_note(self, invoice):
        note = invoice._reverse_moves([{"invoice_date": date(2026, 6, 15)}])
        note.action_post()
        return note

    def _body(self, envelope, operation):
        return envelope.find(f"{{{SOAP_NS}}}Body/{{{SERVICE_NS}}}{operation}")

    def test_invoice_request_follows_the_proven_envelope(self):
        # the exact shape production submits with: the identification and
        # the base64 document wrapped in <factura>, signed with the
        # credentials of the invoice's company
        invoice = self._posted_invoice()
        record = self._pending(invoice)
        transport = _FakeTransport(_invoice_answer("S"))
        self._send(record, transport)
        self.assertEqual(record.edi_exchange_state, "output_sent_and_processed")
        self.assertFalse(record.exchange_error)
        self.assertEqual(len(transport.envelopes), 1)
        envelope = transport.envelopes[0]
        self.assertEqual(envelope.find(f".//{{{WSSE_NS}}}Username").text, "test-user")
        self.assertEqual(envelope.find(f".//{{{WSSE_NS}}}Password").text, "test-secret")
        operation = self._body(envelope, "submeterFacturaElectronicaCRD")
        self.assertIsNotNone(operation)
        self.assertEqual([child.tag for child in operation], ["factura"])
        self.assertEqual(
            [(child.tag, child.text) for child in operation[0]],
            [
                ("areaConferencia", "3"),
                ("codigoPrestador", "12345678"),
                ("dataFactura", "2026-05-31"),
                ("nif", "999999990"),
                ("numeroFactura", invoice._get_spms_invoice_number()),
                ("documento", base64.b64encode(DOCUMENT).decode()),
                ("ficheiroComprimido", "N"),
            ],
        )

    def test_credit_note_request_identifies_the_original_invoice(self):
        # a credit note goes through the note operation, wrapped in
        # <nota>: the identification is the original invoice's, the note
        # itself travels as tipoNota/numeroNota
        invoice = self._posted_invoice()
        note = self._posted_credit_note(invoice)
        record = self._pending(note)
        transport = _FakeTransport(_note_answer("S"))
        self._send(record, transport)
        self.assertEqual(record.edi_exchange_state, "output_sent_and_processed")
        operation = self._body(transport.envelopes[0], "submeterNotaCredDebCRD")
        self.assertIsNotNone(operation)
        self.assertEqual([child.tag for child in operation], ["nota"])
        self.assertEqual(
            [(child.tag, child.text) for child in operation[0]],
            [
                ("areaConferencia", "3"),
                ("codigoPrestador", "12345678"),
                ("dataFactura", "2026-05-31"),
                ("nif", "999999990"),
                ("numeroFactura", invoice._get_spms_invoice_number()),
                ("tipoNota", "C"),
                ("numeroNota", note._get_spms_invoice_number()),
                ("documento", base64.b64encode(DOCUMENT).decode()),
            ],
        )

    def test_rejected_document_is_an_error_on_the_record(self):
        # "aceite" other than S: the CCF's answer, verbatim, on the record
        for move, answer in (
            (self._posted_invoice(), _invoice_answer("N")),
            (self._posted_credit_note(self._posted_invoice()), _note_answer("N")),
        ):
            with self.subTest(move_type=move.move_type):
                record = self._pending(move)
                self._send(record, _FakeTransport(answer))
                self.assertEqual(record.edi_exchange_state, "output_error_on_send")
                self.assertTrue(
                    record.exchange_error.startswith("Invoice not accepted: ")
                )
                self.assertIn("<aceite>N</aceite>", record.exchange_error)

    def test_answer_off_the_contract_is_an_error_on_the_record(self):
        # an answer zeep cannot read against the shipped WSDL is neither
        # an acceptance nor a refusal: the record shows what came back
        record = self._pending(self._posted_invoice())
        self._send(record, _FakeTransport(_flat_invoice_answer("S")))
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertIn("could not be interpreted", record.exchange_error)
        self.assertIn("<aceite>S</aceite>", record.exchange_error)

    def test_http_error_answers_are_retried(self):
        # a fault or an empty 500 is not a verdict on the document: the
        # queue retries the job, as it did with the HTTPError before, and
        # the answer travels in the error
        for answer, detail in (
            (_fault_answer("Authentication failed"), "Authentication failed"),
            (_empty_body_answer(), "Unknown fault occured"),
        ):
            with self.subTest(detail=detail):
                record = self._pending(self._posted_invoice())
                with self.assertRaisesRegex(RetryableJobError, detail) as raised:
                    self._send(record, _FakeTransport(answer))
                self.assertIn("SPMS response body", str(raised.exception))
                self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_connection_problems_are_retried(self):
        for side_effect in (
            requests.ConnectionError("connection refused"),
            requests.Timeout("timed out"),
        ):
            with self.subTest(side_effect=type(side_effect).__name__):
                record = self._pending(self._posted_invoice())
                with self.assertRaises(RetryableJobError):
                    self._send(record, _FakeTransport(side_effect=side_effect))
                self.assertEqual(record.edi_exchange_state, "output_pending")
