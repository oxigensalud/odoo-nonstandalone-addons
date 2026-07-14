# Copyright NuoBiT Solutions - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)


def migrate(cr, version):
    """Deliberately orphan the legacy EDI format record.

    The 18 port stops shipping ``spms_edi_format`` (the module left the
    legacy ``account_edi`` framework), but historical
    ``account_edi_document`` rows keep referencing the record by FK, so it
    can never be deleted. Without this, Odoo's data GC (``_process_end``)
    re-attempts the doomed delete on every module update and aborts the
    registry load with a ForeignKeyViolation.

    Removing the ``ir.model.data`` row detaches the record from the
    module: it stays in the database as plain historical data and no
    update ever tries to garbage-collect it again.
    """
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'l10n_pt_invoice_spms'
           AND name = 'spms_edi_format'
        """
    )
