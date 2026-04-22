"""Eval package: golden-set checker and LLM-as-judge.

Kept separate from :mod:`snaq_verify` because the eval surface should
not import from -- or depend on -- the agent's internal modules beyond
its public output shape.
"""
