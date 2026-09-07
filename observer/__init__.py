"""Silent observation layer (spec section 17).

STRICTLY OFFLINE. Nothing in this package is imported by the patient engine
and nothing here runs during an encounter. It reads a finished turn log.

This separation is the point: patient behaviour and learner assessment must
stay independent, so an observer can never nudge what the patient says.
"""
