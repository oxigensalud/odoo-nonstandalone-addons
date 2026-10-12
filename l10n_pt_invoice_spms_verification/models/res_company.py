# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    spms_adjustment_limit = fields.Monetary(
        string="SPMS Adjustment Limit",
        currency_field="currency_id",
        default=0.01,
        help="Largest difference, taxes included, between the official "
        "value and the draft credit or debit note that is written on its "
        "tax line: the rounding cent of a tax computed once on the invoice "
        "total. A larger difference holds the result in error for review "
        "instead of generating the note.",
    )
    spms_regularization_product_id = fields.Many2one(
        comodel_name="product.product",
        string="SPMS Regularization Product",
        domain="[('company_id', 'in', [id, False])]",
        help="Product of the single line that a generated credit or debit note "
        "carries when its claims mix positive and negative differences. "
        "Created from this field, it comes as a service of this company, "
        "neither sold nor purchased.",
    )

    _sql_constraints = [
        (
            "spms_adjustment_limit_positive",
            "CHECK(spms_adjustment_limit >= 0)",
            "The SPMS adjustment limit cannot be negative.",
        ),
    ]
