# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import tempfile
import traceback
from unittest import mock

import requests

from odoo.addons.queue_job.exception import RetryableJobError

from .common import SpmsInvoiceCase

# the CCF reply to submeterFacturaElectronicaCRD, with the fields of its return
CCF_REPLY = """<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
    <S:Body>
        <ns2:submeterFacturaElectronicaCRDResponse
            xmlns:ns2="http://facturaElectronica.service.cc.ccf/"
        >
            <return>{fields}</return>
        </ns2:submeterFacturaElectronicaCRDResponse>
    </S:Body>
</S:Envelope>"""

# a SOAP fault of the CCF, as its WebLogic server answers it
CCF_FAULT = """<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
    <S:Header>
        <work:WorkContext
            xmlns:work="http://oracle.com/weblogic/soap/workarea/"
        >invented-work-context</work:WorkContext>
    </S:Header>
    <S:Body>
        <S:Fault>
            <faultcode>S:Server</faultcode>
            <faultstring>{faultstring}</faultstring>
        </S:Fault>
    </S:Body>
</S:Envelope>"""

# the same answer with two faults, which SOAP does not allow
CCF_TWO_FAULTS = """<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
    <S:Header>
        <work:WorkContext
            xmlns:work="http://oracle.com/weblogic/soap/workarea/"
        >invented-work-context</work:WorkContext>
    </S:Header>
    <S:Body>
        <S:Fault>
            <faultcode>S:Server</faultcode>
            <faultstring>{first}</faultstring>
        </S:Fault>
        <S:Fault>
            <faultcode>S:Server</faultcode>
            <faultstring>{second}</faultstring>
        </S:Fault>
    </S:Body>
</S:Envelope>"""

# a page of a web server in front of the CCF: HTML, not well-formed XML
HTML_PAGE = """<html>
<head><title>Error</title></head>
<body><h1>Service not available</h1><hr><p>Please try again later.</p></body>
</html>"""

# an invented refusal; the CCF puts content codes on LineID 0, which is no invoice line
APPLICATION_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<ApplicationResponse
    xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
>
    <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
    <cac:DocumentResponse>
        <cac:Response>
            <cbc:ReferenceID>1</cbc:ReferenceID>
            <cbc:ResponseCode>T001</cbc:ResponseCode>
            <cbc:Description>{description}</cbc:Description>
        </cac:Response>
        <cac:DocumentReference>
            <cbc:ID>TSPMS202605-1</cbc:ID>
        </cac:DocumentReference>
        <cac:LineResponse>
            <cac:LineReference>
                <cbc:LineID>0</cbc:LineID>
            </cac:LineReference>
            <cac:Response>
                <cbc:ReferenceID>2</cbc:ReferenceID>
                <cbc:ResponseCode>T101</cbc:ResponseCode>
                <cbc:Description>Linha com período já faturado</cbc:Description>
            </cac:Response>
            <cac:Response>
                <cbc:ReferenceID>3</cbc:ReferenceID>
                <cbc:ResponseCode>T102</cbc:ResponseCode>
                <cbc:Description>Código de convenção desconhecido</cbc:Description>
            </cac:Response>
        </cac:LineResponse>
    </cac:DocumentResponse>
</ApplicationResponse>"""

# a refusal in two DocumentResponses, whose responses give a line or do not
APPLICATION_RESPONSE_IN_PARTS = """<ApplicationResponse
    xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
>
    <cac:DocumentResponse>
        <cac:Response>
            <cbc:Description>Resposta sem código</cbc:Description>
        </cac:Response>
        <cac:Response>
            <cbc:ResponseCode>T001</cbc:ResponseCode>
            <cbc:Description>Primeira descrição</cbc:Description>
            <cbc:Description>Segunda descrição</cbc:Description>
        </cac:Response>
        <cac:Response>
            <cbc:ResponseCode>T002</cbc:ResponseCode>
            <cbc:Description> </cbc:Description>
        </cac:Response>
        <cac:Response>
            <cbc:ResponseCode>T003</cbc:ResponseCode>
            <cbc:ResponseCode>T004</cbc:ResponseCode>
            <cbc:Description>Resposta com dois códigos</cbc:Description>
        </cac:Response>
        <cac:Response>
            <cbc:ResponseCode/>
            <cbc:Description>Resposta com código vazio</cbc:Description>
        </cac:Response>
        <cac:Response>
            <cbc:ResponseCode>T005<!-- comentário --></cbc:ResponseCode>
            <cbc:Description>Resposta com código comentado</cbc:Description>
        </cac:Response>
        <cac:LineResponse>
            <cac:LineReference/>
            <cac:Response>
                <cbc:ResponseCode>T101</cbc:ResponseCode>
            </cac:Response>
        </cac:LineResponse>
        <cac:LineResponse>
            <cac:LineReference>
                <cbc:LineID>2</cbc:LineID>
            </cac:LineReference>
            <cac:Response>
                <cbc:ResponseCode>T201</cbc:ResponseCode>
            </cac:Response>
        </cac:LineResponse>
    </cac:DocumentResponse>
    <cac:DocumentResponse>
        <cac:Response>
            <cbc:ResponseCode>T301</cbc:ResponseCode>
            <cbc:Description>Resposta ao segundo documento</cbc:Description>
        </cac:Response>
        <cac:LineResponse>
            <cac:LineReference>
                <cbc:LineID>0</cbc:LineID>
            </cac:LineReference>
            <cac:Response>
                <cbc:ResponseCode>T401</cbc:ResponseCode>
            </cac:Response>
        </cac:LineResponse>
    </cac:DocumentResponse>
</ApplicationResponse>"""

# a refusal that carries no response at all
APPLICATION_RESPONSE_WITHOUT_RESPONSES = """<ApplicationResponse
    xmlns="urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
>
    <cac:DocumentResponse/>
</ApplicationResponse>"""

# not a refusal, though it holds a DocumentResponse with a response code
NOT_AN_APPLICATION_RESPONSE = """<Invoice
    xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
>
    <cac:DocumentResponse>
        <cac:Response>
            <cbc:ResponseCode>T001</cbc:ResponseCode>
        </cac:Response>
    </cac:DocumentResponse>
</Invoice>"""


class TestSpmsSend(SpmsInvoiceCase):
    """What reaches SPMS when a move is sent."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # credentials that no SPMS text of these tests contains
        cls.company.spms_username = "test-user"
        cls.company.spms_password = "test-secret"

    def _sending_record_with_document(self, move, state):
        record = self._sending_record(move, state)
        record._set_file_content("<Invoice/>")
        return record

    def _ccf_reply(self, status_code, content):
        response = requests.Response()
        response.status_code = status_code
        # the server's own reason phrase, which the module must not use
        response.reason = "Odd reason from the server"
        response._content = content.encode()
        return response

    def _documento(self, content):
        # base64 in lines of 76 characters, as the CCF sends it
        encoded = base64.b64encode(content.encode()).decode()
        return "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))

    def _exception_chain(self, exception):
        # the exception and its causes, as queue_job stores them on a failed job
        return "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )

    def test_posted_invoice_is_sent(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        acceptance = CCF_REPLY.format(fields="<aceite>S</aceite>")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, acceptance)
        ) as post:
            record.action_exchange_send()
        post.assert_called_once()
        self.assertEqual(record.edi_exchange_state, "output_sent_and_processed")

    def test_verdict_padded_with_spaces_is_accepted(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        acceptance = CCF_REPLY.format(fields="<aceite> S </aceite>")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, acceptance)
        ):
            record.action_exchange_send()
        self.assertEqual(record.edi_exchange_state, "output_sent_and_processed")

    def test_invoice_refused_by_spms_is_left_in_error_with_its_reasons(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        documento = self._documento(
            APPLICATION_RESPONSE.format(description="Documento com estrutura inválida")
        )
        refusal = CCF_REPLY.format(
            fields=(
                "<areaConferencia>3</areaConferencia>"
                "<codigoPrestador>1234567</codigoPrestador>"
                "<dataFactura>2026-05-31</dataFactura>"
                "<nif>500000000</nif>"
                "<numeroFactura>TSPMS202605-1</numeroFactura>"
                "<documento>%s</documento>"
                "<aceite>N</aceite>"
            )
            % documento
        )
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, refusal)
        ):
            record.action_exchange_send()
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertEqual(
            record.exchange_error,
            "SPMS did not accept TSPMS/2026/05/0001.\n"
            "T001: Documento com estrutura inválida\n"
            "T101: Linha com período já faturado\n"
            "T102: Código de convenção desconhecido",
        )
        self.assertIn("UserError", record.exchange_error_traceback)
        # a line of the base64 as the CCF sent it
        base64_line = documento.splitlines()[0]
        self.assertNotIn(base64_line, record.exchange_error)
        self.assertNotIn(base64_line, record.exchange_error_traceback)

    def test_refusal_without_a_readable_reason_keeps_its_first_line_only(self):
        invoice = self._create_invoice()
        invoice.action_post()
        documento = self._documento(
            APPLICATION_RESPONSE.format(description="Documento com estrutura inválida")
        )
        for case, fields in (
            ("no documento", "<aceite>N</aceite>"),
            (
                "two documentos",
                "<documento>%s</documento><documento>%s</documento><aceite>N</aceite>"
                % (documento, documento),
            ),
            ("not base64", "<documento>sem base64!</documento><aceite>N</aceite>"),
            (
                "a character out of the base64 alphabet",
                "<documento>%s</documento><aceite>N</aceite>"
                % (documento[:10] + "!" + documento[10:]),
            ),
            (
                "not XML",
                "<documento>%s</documento><aceite>N</aceite>"
                % self._documento("sem XML"),
            ),
            (
                "not an ApplicationResponse",
                "<documento>%s</documento><aceite>N</aceite>"
                % self._documento(NOT_AN_APPLICATION_RESPONSE),
            ),
            (
                "no response",
                "<documento>%s</documento><aceite>N</aceite>"
                % self._documento(APPLICATION_RESPONSE_WITHOUT_RESPONSES),
            ),
        ):
            with self.subTest(case=case):
                record = self._sending_record_with_document(invoice, "output_pending")
                refusal = CCF_REPLY.format(fields=fields)
                with mock.patch.object(
                    requests, "post", return_value=self._ccf_reply(200, refusal)
                ):
                    record.action_exchange_send()
                self.assertEqual(record.edi_exchange_state, "output_error_on_send")
                self.assertEqual(
                    record.exchange_error, "SPMS did not accept TSPMS/2026/05/0001."
                )

    def test_unreadable_parts_of_a_refusal_reason_are_left_out(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        refusal = CCF_REPLY.format(
            fields="<documento>%s</documento><aceite>N</aceite>"
            % self._documento(APPLICATION_RESPONSE_IN_PARTS)
        )
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, refusal)
        ):
            record.action_exchange_send()
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertEqual(
            record.exchange_error,
            "SPMS did not accept TSPMS/2026/05/0001.\n"
            "T001: Primeira descrição; Segunda descrição\n"
            "T002\n"
            "T101\n"
            "T201\n"
            "T301: Resposta ao segundo documento\n"
            "T401",
        )

    def test_our_credentials_in_a_refusal_are_masked(self):
        self.company.spms_username = "test-user"
        self.company.spms_password = "test-secret"
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        documento = self._documento(
            APPLICATION_RESPONSE.format(
                description="Utilizador test-user sem acesso, senha test-secret"
            )
        )
        refusal = CCF_REPLY.format(
            fields="<documento>%s</documento><aceite>N</aceite>" % documento
        )
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, refusal)
        ):
            record.action_exchange_send()
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertEqual(
            record.exchange_error,
            "SPMS did not accept TSPMS/2026/05/0001.\n"
            "T001: Utilizador *** sem acesso, senha ***\n"
            "T101: Linha com período já faturado\n"
            "T102: Código de convenção desconhecido",
        )
        for value in ("test-user", "test-secret"):
            self.assertNotIn(value, record.exchange_error)
            self.assertNotIn(value, record.exchange_error_traceback)

    def test_unreadable_answer_is_left_in_error(self):
        invoice = self._create_invoice()
        invoice.action_post()
        for case, answer in (
            ("no verdict", CCF_REPLY.format(fields="")),
            (
                "two verdicts",
                CCF_REPLY.format(fields="<aceite>S</aceite><aceite>S</aceite>"),
            ),
            ("unknown verdict", CCF_REPLY.format(fields="<aceite>X</aceite>")),
            ("empty verdict", CCF_REPLY.format(fields="<aceite/>")),
            (
                "verdict with an entity",
                '<!DOCTYPE S:Envelope [<!ENTITY x "N">]>'
                + CCF_REPLY.format(fields="<aceite>S&x;</aceite>"),
            ),
            (
                "verdict with a comment",
                CCF_REPLY.format(fields="<aceite>S<!--c-->N</aceite>"),
            ),
            ("HTML page", HTML_PAGE),
        ):
            with self.subTest(case=case):
                record = self._sending_record_with_document(invoice, "output_pending")
                with mock.patch.object(
                    requests, "post", return_value=self._ccf_reply(200, answer)
                ):
                    record.action_exchange_send()
                self.assertEqual(record.edi_exchange_state, "output_error_on_send")
                self.assertEqual(
                    record.exchange_error,
                    "The answer from SPMS could not be read, so it is not known "
                    "whether TSPMS/2026/05/0001 was accepted.",
                )

    def test_malformed_answer_is_left_out_of_the_error(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        malformed = "<html><body><leak-marker></body></html>"
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, malformed)
        ):
            record.action_exchange_send()
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertEqual(
            record.exchange_error,
            "The answer from SPMS could not be read, so it is not known "
            "whether TSPMS/2026/05/0001 was accepted.",
        )
        for value in ("leak-marker", "XMLSyntaxError"):
            self.assertNotIn(value, record.exchange_error)
            self.assertNotIn(value, record.exchange_error_traceback)

    def test_unanswered_send_is_left_to_retry(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        with mock.patch.object(
            requests, "post", side_effect=requests.Timeout("read timed out")
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(str(raised.exception), "read timed out")
        self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_echoed_request_is_left_out_of_the_error(self):
        # SPMS answers most errors with the request it was sent
        self.company.spms_username = "test-user"
        self.company.spms_password = "test-secret"
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")

        def echo_request(url, data, headers):
            response = requests.Response()
            response.status_code = 500
            response.reason = "Odd reason from the server"
            response._content = data.encode()
            return response

        with mock.patch.object(
            requests, "post", side_effect=echo_request
        ) as post, self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 500 Internal Server Error); "
            "sending TSPMS/2026/05/0001 will be retried automatically.",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")
        chain = self._exception_chain(raised.exception)
        self.assertIn("HTTPError", chain)
        sent = post.call_args[1]["data"]
        # the credentials and the document, <Invoice/> in base64
        for value in ("test-user", "test-secret", "PEludm9pY2UvPg=="):
            self.assertIn(value, sent)
            self.assertNotIn(value, chain)
        self.assertNotIn("Odd reason from the server", chain)

    def test_soap_fault_message_is_kept_in_the_error(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        fault = CCF_FAULT.format(faultstring="998 - Falha inventada para o teste")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(500, fault)
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 500 Internal Server Error); "
            "sending TSPMS/2026/05/0001 will be retried automatically. "
            "SPMS message: 998 - Falha inventada para o teste",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_soap_fault_texts_are_stripped_joined_and_empty_ones_dropped(self):
        invoice = self._create_invoice()
        invoice.action_post()
        for case, fault, message in (
            (
                "empty fault",
                CCF_FAULT.format(faultstring=""),
                "SPMS answered with an error (HTTP 500 Internal Server Error); "
                "sending TSPMS/2026/05/0001 will be retried automatically.",
            ),
            (
                "padded fault",
                CCF_FAULT.format(
                    faultstring="\n   998 - Falha inventada para o teste  \n"
                ),
                "SPMS answered with an error (HTTP 500 Internal Server Error); "
                "sending TSPMS/2026/05/0001 will be retried automatically. "
                "SPMS message: 998 - Falha inventada para o teste",
            ),
            (
                "two faults, the first one empty",
                CCF_TWO_FAULTS.format(first="", second="997 - Outra falha inventada"),
                "SPMS answered with an error (HTTP 500 Internal Server Error); "
                "sending TSPMS/2026/05/0001 will be retried automatically. "
                "SPMS message: 997 - Outra falha inventada",
            ),
            (
                "two faults",
                CCF_TWO_FAULTS.format(
                    first="998 - Falha inventada para o teste",
                    second="997 - Outra falha inventada",
                ),
                "SPMS answered with an error (HTTP 500 Internal Server Error); "
                "sending TSPMS/2026/05/0001 will be retried automatically. "
                "SPMS message: 998 - Falha inventada para o teste; "
                "997 - Outra falha inventada",
            ),
        ):
            with self.subTest(case=case):
                record = self._sending_record_with_document(invoice, "output_pending")
                with mock.patch.object(
                    requests, "post", return_value=self._ccf_reply(500, fault)
                ), self.assertRaises(RetryableJobError) as raised:
                    record.action_exchange_send()
                self.assertEqual(str(raised.exception), message)
                self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_our_credentials_in_a_fault_are_masked(self):
        self.company.spms_username = "test-user"
        self.company.spms_password = "test-secret"
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        fault = CCF_FAULT.format(
            faultstring="Utilizador test-user sem acesso, senha test-secret"
        )
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(500, fault)
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 500 Internal Server Error); "
            "sending TSPMS/2026/05/0001 will be retried automatically. "
            "SPMS message: Utilizador *** sem acesso, senha ***",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")
        chain = self._exception_chain(raised.exception)
        for value in ("test-user", "test-secret"):
            self.assertNotIn(value, str(raised.exception))
            self.assertNotIn(value, chain)

    def test_password_starting_with_the_username_is_masked_whole(self):
        self.company.spms_username = "test-user"
        self.company.spms_password = "test-user-secret"
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        fault = CCF_FAULT.format(
            faultstring="Utilizador test-user sem acesso, senha test-user-secret"
        )
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(500, fault)
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 500 Internal Server Error); "
            "sending TSPMS/2026/05/0001 will be retried automatically. "
            "SPMS message: Utilizador *** sem acesso, senha ***",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_spms_text_is_kept_unchanged_without_a_password(self):
        self.company.spms_username = "test-user"
        self.company.spms_password = ""
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        fault = CCF_FAULT.format(faultstring="998 - Falha inventada para o teste")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(500, fault)
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 500 Internal Server Error); "
            "sending TSPMS/2026/05/0001 will be retried automatically. "
            "SPMS message: 998 - Falha inventada para o teste",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_external_entity_of_the_answer_is_not_resolved(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as local_file:
            local_file.write("invented-local-file-content")
            local_file.flush()
            doctype = '<!DOCTYPE S:Envelope [<!ENTITY xxe SYSTEM "file://%s">]>' % (
                local_file.name
            )
            fault = doctype + CCF_FAULT.format(faultstring="&xxe;")
            with mock.patch.object(
                requests, "post", return_value=self._ccf_reply(500, fault)
            ), self.assertRaises(RetryableJobError) as raised:
                record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 500 Internal Server Error); "
            "sending TSPMS/2026/05/0001 will be retried automatically.",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")
        chain = self._exception_chain(raised.exception)
        self.assertIn("HTTPError", chain)
        self.assertNotIn("invented-local-file-content", chain)

    def test_bad_request_is_left_to_retry(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(400, "")
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 400 Bad Request); "
            "sending TSPMS/2026/05/0001 will be retried automatically.",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_error_page_is_left_out_of_the_error(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(502, HTML_PAGE)
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 502 Bad Gateway); "
            "sending TSPMS/2026/05/0001 will be retried automatically.",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_malformed_error_answer_is_left_out_of_the_error(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        malformed = "<html><body><leak-marker></body></html>"
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(502, malformed)
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 502 Bad Gateway); "
            "sending TSPMS/2026/05/0001 will be retried automatically.",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")
        chain = self._exception_chain(raised.exception)
        self.assertIn("HTTPError", chain)
        for value in ("leak-marker", "XMLSyntaxError"):
            self.assertNotIn(value, chain)

    def test_status_without_standard_reason_is_named_by_its_code(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(599, "")
        ), self.assertRaises(RetryableJobError) as raised:
            record.action_exchange_send()
        self.assertEqual(
            str(raised.exception),
            "SPMS answered with an error (HTTP 599); "
            "sending TSPMS/2026/05/0001 will be retried automatically.",
        )
        self.assertEqual(record.edi_exchange_state, "output_pending")

    def test_cancelled_invoice_is_not_sent_on_retry(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_error_on_send")
        invoice.button_cancel()
        acceptance = CCF_REPLY.format(fields="<aceite>S</aceite>")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, acceptance)
        ) as post:
            record.action_retry()
            record.action_exchange_send()
        post.assert_not_called()
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertEqual(
            record.exchange_error,
            "TSPMS/2026/05/0001 is not posted, so it cannot be sent to SPMS.",
        )

    def test_invoice_reset_to_draft_is_not_sent(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        invoice.button_draft()
        acceptance = CCF_REPLY.format(fields="<aceite>S</aceite>")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, acceptance)
        ) as post:
            record.action_exchange_send()
        post.assert_not_called()
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertEqual(
            record.exchange_error,
            "Draft Invoice TSPMS/2026/05/0001 is not posted, "
            "so it cannot be sent to SPMS.",
        )

    def test_never_posted_draft_is_not_sent(self):
        # the first move of a journal is numbered even as a draft: post one first
        self._create_invoice().action_post()
        invoice = self._create_invoice()
        self.assertEqual(invoice.name, "/")
        record = self._sending_record_with_document(invoice, "output_pending")
        acceptance = CCF_REPLY.format(fields="<aceite>S</aceite>")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply(200, acceptance)
        ) as post:
            record.action_exchange_send()
        post.assert_not_called()
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertEqual(
            record.exchange_error,
            "Draft Invoice (* %s) is not posted, so it cannot be sent to SPMS."
            % invoice.id,
        )

    def test_spms_button_is_offered_on_posted_invoices_only(self):
        draft = self._create_invoice()
        posted = self._create_invoice()
        posted.action_post()
        cancelled = self._create_invoice()
        cancelled.action_post()
        cancelled.button_cancel()
        for invoice, offered in (
            (draft, False),
            (posted, True),
            (cancelled, False),
        ):
            with self.subTest(state=invoice.state):
                visible_types = {
                    config["type"]["id"] for config in invoice.edi_config.values()
                }
                self.assertEqual(self.exchange_type.id in visible_types, offered)
