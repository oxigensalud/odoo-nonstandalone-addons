# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest import mock

import requests

from odoo.addons.queue_job.exception import RetryableJobError

from .common import SpmsInvoiceCase

# the CCF reply to submeterFacturaElectronicaCRD, cut to what the module reads
CCF_REPLY = """<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/">
    <S:Body>
        <ns2:submeterFacturaElectronicaCRDResponse
            xmlns:ns2="http://facturaElectronica.service.cc.ccf/"
        >
            <return><aceite>{aceite}</aceite></return>
        </ns2:submeterFacturaElectronicaCRDResponse>
    </S:Body>
</S:Envelope>"""


class TestSpmsSend(SpmsInvoiceCase):
    """What reaches SPMS when a move is sent."""

    def _sending_record_with_document(self, move, state):
        record = self._sending_record(move, state)
        record._set_file_content("<Invoice/>")
        return record

    def _ccf_reply(self, aceite):
        return mock.Mock(content=CCF_REPLY.format(aceite=aceite).encode())

    def test_posted_invoice_is_sent(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply("S")
        ) as post:
            record.action_exchange_send()
        post.assert_called_once()
        self.assertEqual(record.edi_exchange_state, "output_sent_and_processed")

    def test_invoice_refused_by_spms_is_left_in_error(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        with mock.patch.object(requests, "post", return_value=self._ccf_reply("N")):
            record.action_exchange_send()
        self.assertEqual(record.edi_exchange_state, "output_error_on_send")
        self.assertIn("Invoice not accepted", record.exchange_error)

    def test_unanswered_send_is_left_to_retry(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_pending")
        with mock.patch.object(
            requests, "post", side_effect=requests.Timeout("read timed out")
        ), self.assertRaises(RetryableJobError):
            record.action_exchange_send()
        self.assertEqual(record.edi_exchange_state, "output_pending")
        self.assertEqual(record.exchange_error, "read timed out")

    def test_cancelled_invoice_is_not_sent_on_retry(self):
        invoice = self._create_invoice()
        invoice.action_post()
        record = self._sending_record_with_document(invoice, "output_error_on_send")
        invoice.button_cancel()
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply("S")
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
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply("S")
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
        with mock.patch.object(
            requests, "post", return_value=self._ccf_reply("S")
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
