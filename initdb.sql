-- Init scripts run connected to POSTGRES_DB (the scenario's database); create its "_test" twin.
SELECT format('CREATE DATABASE %I', current_database() || '_test') \gexec
