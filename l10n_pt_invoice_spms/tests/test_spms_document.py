# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from .common import SpmsInvoiceCase

CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"


class TestSpmsDocument(SpmsInvoiceCase):
    """The UBL document the sender builds for each kind of move."""

    def test_credit_note_document_names_the_invoice_by_its_invoice_date(self):
        invoice = self._create_invoice(
            invoice_date=date(2026, 5, 30), accounting_date=date(2026, 5, 31)
        )
        invoice.action_post()
        credit_note = self._create_credit_note(invoice, date(2026, 6, 15))
        credit_note.action_post()
        document = self._document(credit_note)
        reference = document.find(
            CAC + "BillingReference/" + CAC + "InvoiceDocumentReference"
        )
        self.assertEqual(reference.findtext(CBC + "IssueDate"), "2026-05-30")
