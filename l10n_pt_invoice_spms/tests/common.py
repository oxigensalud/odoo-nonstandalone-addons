# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo.tests.common import SavepointCase


class SpmsInvoiceCase(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.country_id = cls.env.ref("base.pt")
        cls.spanish_company = cls.env["res.company"].create(
            {
                "name": "SPMS Spanish test company",
                "country_id": cls.env.ref("base.es").id,
                "currency_id": cls.company.currency_id.id,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "SPMS shared test customer", "spms_information": True}
        )
        cls.product = cls.env["product.product"].create(
            {"name": "SPMS test service", "type": "service"}
        )
        cls.journals = {}
        cls.income_accounts = {}
        cls.taxes = {}
        for company in cls.company + cls.spanish_company:
            accounts = cls.env["account.account"].with_company(company)
            income = accounts.create(
                {
                    "name": "SPMS test revenue",
                    "code": "SPREV",
                    "company_id": company.id,
                    "user_type_id": cls.env.ref("account.data_account_type_revenue").id,
                }
            )
            receivable = accounts.create(
                {
                    "name": "SPMS test receivable",
                    "code": "SPREC",
                    "company_id": company.id,
                    "reconcile": True,
                    "user_type_id": cls.env.ref(
                        "account.data_account_type_receivable"
                    ).id,
                }
            )
            cls.partner.with_company(
                company
            ).property_account_receivable_id = receivable
            journal = (
                cls.env["account.journal"]
                .with_company(company)
                .create(
                    {
                        "name": "SPMS test sales",
                        "code": "TSPMS",
                        "type": "sale",
                        "company_id": company.id,
                        "default_account_id": income.id,
                        "edi_format_ids": [(5, 0, 0)],
                    }
                )
            )
            cls.journals[company.id] = journal
            cls.income_accounts[company.id] = income
            cls.taxes[company.id] = (
                cls.env["account.tax"]
                .with_company(company)
                .create(
                    {
                        "name": "SPMS test sales tax",
                        "type_tax_use": "sale",
                        "amount_type": "percent",
                        "amount": 23,
                        "company_id": company.id,
                    }
                )
            )
        cls.journal = cls.journals[cls.company.id]
        cls.backend = cls.env.ref("l10n_pt_invoice_spms.spms_backend")
        cls.exchange_type = cls.env.ref("l10n_pt_invoice_spms.spms_exchange_type")

    @classmethod
    def _create_invoice(cls, company=None):
        company = company if company is not None else cls.company
        move = (
            cls.env["account.move"]
            .with_company(company)
            .create(
                {
                    "move_type": "out_invoice",
                    "partner_id": cls.partner.id,
                    "journal_id": cls.journals[company.id].id,
                    "invoice_date": date(2026, 5, 31),
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": cls.product.id,
                                "account_id": cls.income_accounts[company.id].id,
                                "quantity": 1,
                                "price_unit": 100.0,
                                "tax_ids": [(6, 0, cls.taxes[company.id].ids)],
                            },
                        )
                    ],
                }
            )
        )
        return move

    def _sending_record(self, move, state):
        return self.backend.create_record(
            "l10n_pt_spms",
            {"edi_exchange_state": state, "model": "account.move", "res_id": move.id},
        )
