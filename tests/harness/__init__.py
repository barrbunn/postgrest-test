"""Shared harness for the pytest and Behave suites.

Everything here is infrastructure only: ephemeral Postgres, mock servers,
pgrmapper process control, token minting. Tests and Behave steps build on it.
"""
