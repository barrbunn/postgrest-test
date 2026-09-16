#!/bin/sh
set -e

# Creates the roles PostgREST needs: a NOLOGIN anonymous role and a LOGIN
# authenticator role that can switch to it. Values come from the environment
# injected via compose (.env).
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE ${PGRST_DB_ANON_ROLE} NOLOGIN;
    CREATE ROLE ${PGRST_DB_AUTH_ROLE} NOINHERIT LOGIN PASSWORD '${PGRST_DB_AUTH_PASSWORD}';
    GRANT ${PGRST_DB_ANON_ROLE} TO ${PGRST_DB_AUTH_ROLE};
EOSQL
