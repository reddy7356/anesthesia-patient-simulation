"""Mannequin abstraction (spec section 22).

The conversational brain must not know which mannequin it is speaking through.
Today the only implementation is the simulation room's own microphone and
speaker over LiveKit; a vendor SDK becomes a second implementation of the same
interface, not a change to the patient engine.
"""
