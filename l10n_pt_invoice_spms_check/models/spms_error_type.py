# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from psycopg2 import IntegrityError

from odoo import api, fields, models


class SpmsErrorType(models.Model):
    _name = "spms.error.type"
    _description = "SPMS Error Type"
    _order = "code"
    _rec_name = "code"

    code = fields.Char(
        required=True,
        index=True,
        readonly=True,
    )
    description = fields.Char(
        string="Official Message (PT)",
        readonly=True,
        help="Error message exactly as the CCF check documents report it.",
    )

    _sql_constraints = [
        (
            "code_uniq",
            "unique(code)",
            "An error type with this code already exists.",
        ),
    ]

    def name_get(self):
        result = []
        for record in self:
            name = record.code
            if record.description:
                name = "%s - %s" % (record.code, record.description)
            result.append((record.id, name))
        return result

    @api.model
    def _get_or_create(self, code, message=None):
        """Return the error type for ``code``, creating it if unknown.

        The catalogue is a local mirror of a table the CCF owns and only
        publishes through the check documents themselves, so the wire is
        the authority: unknown codes are created on arrival and a changed
        official message overwrites the stored one.
        """
        code = (code or "").strip()
        if not code:
            return self.browse()
        error_type = self.search([("code", "=", code)], limit=1)
        if not error_type:
            try:
                with self.env.cr.savepoint():
                    return self.create({"code": code, "description": message})
            except IntegrityError:
                error_type = self.search([("code", "=", code)], limit=1)
        if message and error_type.description != message:
            error_type.description = message
        return error_type
