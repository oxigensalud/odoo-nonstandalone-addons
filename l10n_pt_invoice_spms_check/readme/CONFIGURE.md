- Assign the *SPMS / Consultation* group to users who can read the
  check results, and *SPMS / Responsible* to the users allowed to work
  with them and with the generated credit and debit notes.
- The polling scheduled action (*SPMS: get conference results from the
  CCF*, under *Settings → Technical → Automation → Scheduled Actions*)
  runs hourly out of the box. It creates the expected result record of
  every posted invoice sent to SPMS that has no definitive check result
  yet, and asks the CCF about each waiting record in its own queue job,
  with each company's own SPMS credentials, so a database where nothing
  was sent makes no calls. Every answer of the CCF — including a
  transport failure — is written on the result record; a job fails only
  on a software failure, stays visible under *Queue → Jobs*, and the
  next pass re-activates that same job rather than queueing another. To
  pause the polling, deactivate the scheduled action — never uninstall
  the module just to stop the calls.
- Set the *SPMS Adjustment Limit* on the company (SPMS page, visible to
  *Technical Settings* users; 0.01 by default): the largest difference,
  taxes included, between the official value and the draft credit or
  debit note that is written on its tax line. A larger difference holds
  the result in *Error* for review instead of generating the note; raise
  the limit only when a larger difference is legitimate and strictly
  necessary.
- Keep the tax rounding method of the company (*Accounting → Settings →
  Taxes → Rounding Method*) on *Round Globally*: the CCF computes the tax
  once on the invoice total, and per-line rounding drifts away from it by
  more than a cent on long credit notes, which then hold in *Error*.
