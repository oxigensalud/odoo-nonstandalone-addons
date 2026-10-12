# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import SpmsVerificationCase


class TestSpmsVerificationFiscalDocument(SpmsVerificationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ptplus on, and posting needs a tax of one of its VAT groups
        cls.company.pt_invoicing = True
        cls.tax6.tax_group_id = cls.env.ref("ptplus.tax_group_iva_6")

    def test_generate_from_another_company_creates_fiscal_credit_note(self):
        move = self._standard_invoice()
        result = self._create_result(move, self._standard_rows(), credit_official=38.16)
        # jobs run as a user of another company
        other_company = self.env["res.company"].create({"name": "Other company"})
        self.env.user.company_id = other_company
        draft = result._generate_note()
        self.assertEqual(draft.fiscal_document_type_id.type, "NC")

    def test_generate_mixed_claims_note_can_be_posted(self):
        # the certification refuses a note with lines of both signs: the
        # rejected TESTP020 and the TESTP021 allowed above the billed amount
        # go on one line, and the credit note posts
        self.company.spms_regularization_product_id = self.env[
            "product.product"
        ].create(
            {
                "name": "SPMS Regularization",
                "type": "service",
                "sale_ok": False,
                "purchase_ok": False,
                "company_id": self.company.id,
            }
        )
        move = self._create_invoice(
            "FT 2026/00140", [("TESTP020", 31, 1.0), ("TESTP021", 10, 2.0)]
        )
        rows = [
            {
                "prescription": "TESTP020",
                "billed": 31.0,
                "allowed": 0.0,
                "days_billed": 31.0,
            },
            {
                "prescription": "TESTP021",
                "billed": 20.0,
                "allowed": 22.0,
                "days_billed": 10.0,
                "days_paid": 11.0,
            },
        ]
        result = self._create_result(move, rows, credit_official=30.74)
        draft = result._generate_note()
        draft.action_post()
        self.assertEqual(draft.state, "posted")
        self.assertEqual(draft.fiscal_document_type_id.type, "NC")

    def test_generate_from_another_company_creates_fiscal_debit_note(self):
        move, rows = self._debit_invoice()
        result = self._create_result(move, rows, credit_official=-2.12)
        # jobs run as a user of another company
        other_company = self.env["res.company"].create({"name": "Other company"})
        self.env.user.company_id = other_company
        draft = result._generate_note()
        self.assertEqual(draft.fiscal_document_type_id.type, "ND")
