Feature: pgrmapper routing

  Scenario: known table is forwarded to PostgREST
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id,user_name" returning JSON [{"user_id": 1, "user_name": "alice"}]
    When I request GET "/users" with query "select=user_id,user_name" through the gateway as "editor"
    Then the response status is 200
    And the response JSON is [{"user_id": 1, "user_name": "alice"}]
    And postgrest received the last request "GET /users?select=user_id,user_name"

  Scenario: unknown table returns 404 without touching PostgREST
    Given the scenario "users_grants"
    When I request GET "/nope" through the gateway as "editor"
    Then the response status is 404
    And the response error mentions "no such resource: nope"
    And postgrest received no requests

  Scenario: health endpoint answers directly
    Given the scenario "users_grants"
    When I request GET "/health" directly without a token
    Then the response status is 200
    And the response JSON is {"status": "ok"}

  Scenario: root route is forwarded
    Given the scenario "users_grants"
    And a postgrest response for "GET /" returning JSON {"openapi": "3.0"}
    When I request GET "/" directly without a token
    Then the response status is 200
    And the response JSON is {"openapi": "3.0"}

  Scenario: only GET is served
    Given the scenario "users_grants"
    When I send a POST request to "/users"
    Then the response status is 405
