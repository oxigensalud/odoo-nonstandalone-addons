# Copyright 2025 Dixmit
# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import timedelta

from odoo import models

# the Note the DebitNote profile of the CCF specification requires: the nota,
# the invoice it rectifies and that invoice's date
DEBIT_NOTE_NOTE = (
    "Nota de Débito %(note)s para rectificação à fatura %(invoice)s de %(date)s"
)
# the templates that differ by document: the core builder renders the Invoice
# and the CreditNote; it has no debit note, so the module's own templates
# render the DebitNote profile of the CCF specification
INVOICE_TEMPLATE_VALS = {
    "InvoiceType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_InvoiceType",
    "InvoiceLineType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_InvoiceLine",
}
DEBIT_NOTE_TEMPLATE_VALS = {
    "main_template": "l10n_pt_invoice_spms.spms_cius_pt_211_DebitNote",
    "InvoiceType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_DebitNoteType",
    "InvoiceLineType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_DebitNoteLine",
}


class AccountEdiFormat(models.Model):

    _inherit = "account.edi.format"

    def _get_xml_builder(self, company):
        """Override to return the SPMS XML builder."""
        if self.code == "spms_cius_pt_211" and company._is_spms_company():
            return self.env["account.edi.xml.spms_cius_pt_211"]
        return super()._get_xml_builder(company)


class AccountEdiXmlSpmsCiusPt211(models.AbstractModel):
    _name = "account.edi.xml.spms_cius_pt_211"
    _inherit = "account.edi.xml.ubl_20"
    _description = "SPMS CIUS PT 2.11 XML Builder"

    def _get_invoice_period_vals_list(self, invoice):
        origin_invoice = invoice._spms_origin_invoice() or invoice
        start_date = min(origin_invoice.invoice_line_ids.mapped("spms_start_date"))
        start_date = start_date.replace(day=1)
        if start_date:
            start_date = start_date.isoformat()
        end_date = max(origin_invoice.invoice_line_ids.mapped("spms_end_date"))
        end_date = end_date.replace(day=28)
        end_date += timedelta(days=4)
        end_date = end_date - timedelta(days=end_date.day)
        if end_date:
            end_date = end_date.isoformat()
        return [{"start_date": start_date, "end_date": end_date}]

    def _get_partner_party_vals(self, partner, role):
        vals = super()._get_partner_party_vals(partner, role)
        if role == "customer":
            vals.update(
                {
                    "party_legal_entity_vals": [],
                }
            )
        return vals

    def _export_invoice_vals(self, invoice):
        # EXTENDS account.edi.xml.ubl_20
        vals = super()._export_invoice_vals(invoice)

        vals.update(
            {
                "InvoiceExtension_spms": "l10n_pt_invoice_spms.spms_cius_pt_211_InvoiceExtension_spms",  # noqa: B950
                "PartyType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_PartyType",
                "AddressType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_AddressType",
                "TaxCategoryType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_TaxCategoryType",  # noqa: B950
            }
        )
        vals["vals"].update(
            {
                "id": invoice._get_spms_invoice_number(),
            }
        )
        aggregated_vals = {}
        unmerged_vals = []
        for line in vals["vals"]["invoice_line_vals"]:
            if line["price_vals"]["spms_tipo"]:
                if line["price_vals"]["spms_tipo"] not in aggregated_vals:
                    aggregated_vals[line["price_vals"]["spms_tipo"]] = line
                else:
                    aggregated_vals[line["price_vals"]["spms_tipo"]][
                        "invoiced_quantity"
                    ] += line["invoiced_quantity"]
                    aggregated_vals[line["price_vals"]["spms_tipo"]][
                        "line_extension_amount"
                    ] += line["line_extension_amount"]
                    i = 0
                    for tax in line["tax_total_vals"]:
                        aggregated_vals[line["price_vals"]["spms_tipo"]][
                            "tax_total_vals"
                        ][i]["tax_amount"] += tax["tax_amount"]
                        j = 0
                        for tax_subtotal in tax["tax_subtotal_vals"]:
                            aggregated_vals[line["price_vals"]["spms_tipo"]][
                                "tax_total_vals"
                            ][i]["tax_subtotal_vals"][j]["tax_amount"] += tax_subtotal[
                                "tax_amount"
                            ]
                            j += 1
                        i += 1
            else:
                unmerged_vals.append(line["price_vals"])
        line_vals = list(aggregated_vals.values()) + unmerged_vals
        i = 1
        for line in line_vals:
            line["id"] = str(i)
            i += 1
        vals["vals"]["invoice_line_vals"] = line_vals
        lots = invoice.invoice_line_ids.product_id.spms_lot_id
        lotes = []
        lot_number = 1
        for lot in lots:
            lines = invoice.invoice_line_ids.filtered(
                lambda l: l.product_id.spms_lot_id == lot
            )
            lotes.append(
                {
                    "numero": lot_number,
                    "tipo": lot.code,
                    "amount": sum(lines.mapped("price_subtotal")),
                    "total": len(lines),
                    "dispensas": [
                        {
                            "prescription": line.spms_prescription,
                            "user_number": line.spms_user_number,
                            "beneficiary_number": line.spms_beneficiary_number,
                            "prescription_type": line.spms_prescription_type_id.code,
                            "price_subtotal": line.price_subtotal,
                            "price_unit": line.price_unit,
                            "quantity": int(line.quantity),
                            "context": line.spms_context_id.code,
                            "line_number": line.id,
                            "system": line.product_id.default_code,
                            "suspension_reason": line.spms_suspension_reason_id.code,
                            "start_date": line.spms_start_date
                            and line.spms_start_date.isoformat(),
                            "end_date": line.spms_end_date
                            and line.spms_end_date.isoformat(),
                        }
                        for line in lines
                    ],
                }
            )
            lot_number += 1
        vals["vals"].update(
            {
                "spms_crd_vals": {
                    "valor_total_prestacoes": invoice.amount_total,
                    "currency_dp": invoice.currency_id.decimal_places,
                    "quantidade_total_prestacoes": len(invoice.invoice_line_ids),
                    "numero_lotes": len(lots),
                    "lotes": lotes,
                },
                "profile_id": False,
                "ubl_version_id": "UBL 2.0 CS (2006.10) + SIC (2007.03)",
                "customization_id": "1.0",
                "due_date": False,
                "invoice_type_code": "FF",
                "customer_assigned_account_id": invoice.partner_id.spms_assigned_id,
            }
        )
        note_type = invoice._spms_note_type()
        if note_type is None:
            template_vals = INVOICE_TEMPLATE_VALS
            document_vals = {"is_spms_invoice": True}
        elif note_type == "C":
            template_vals = INVOICE_TEMPLATE_VALS
            document_vals = {
                "is_spms_invoice": False,
                "billing_reference_vals": self._get_billing_reference_vals(
                    invoice._spms_origin_invoice()
                ),
            }
        elif note_type == "D":
            billing_reference_vals = self._get_billing_reference_vals(
                invoice._spms_origin_invoice()
            )
            template_vals = DEBIT_NOTE_TEMPLATE_VALS
            document_vals = {
                "billing_reference_vals": billing_reference_vals,
                "note_vals": self._get_debit_note_note_vals_list(
                    invoice, billing_reference_vals
                ),
            }
        else:
            raise ValueError("unknown SPMS note type %r" % (note_type,))
        vals.update(template_vals)
        vals["vals"].update(document_vals)
        return vals

    def _get_billing_reference_vals(self, origin_invoice):
        # the CCF holds the invoice under its invoice date: the IssueDate of its
        # own document and the dataFactura of every envelope
        return {
            "id": origin_invoice._get_spms_invoice_number(),
            "issue_date": origin_invoice.invoice_date.isoformat(),
        }

    def _get_debit_note_note_vals_list(self, invoice, billing_reference_vals):
        return [
            DEBIT_NOTE_NOTE
            % {
                "note": invoice._get_spms_invoice_number(),
                "invoice": billing_reference_vals["id"],
                "date": billing_reference_vals["issue_date"],
            }
        ]

    def _get_invoice_line_price_vals(self, line):
        result = super()._get_invoice_line_price_vals(line)
        result.update(
            {
                "spms_tipo": line.product_id.spms_lot_id.code
                if line.product_id.spms_lot_id
                else "",
            }
        )
        return result

    def _get_partner_party_tax_scheme_vals_list(self, partner, role):
        result = super()._get_partner_party_tax_scheme_vals_list(partner, role)
        for line in result:
            line["tax_scheme_id"] = "PT IVA"
        return result
