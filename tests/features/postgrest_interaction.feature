Feature: pgrmapper to PostgREST interaction

  Scenario: request headers and response are forwarded
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id" returning JSON [{"user_id": 1}]
    When I request GET "/users" with query "select=user_id" through the gateway as "editor"
    Then the response status is 200
    And the response content type is "application/json"
    And the last postgrest request has header "authorization" starting with "Bearer "
    And the last postgrest request has header "accept" equal to "application/json"

  Scenario: upstream error status and body pass through
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id" with status 400 and JSON {"code": "42703", "message": "column users.x does not exist"}
    When I request GET "/users" with query "select=user_id" through the gateway as "editor"
    Then the response status is 400
    And the response JSON is {"code": "42703", "message": "column users.x does not exist"}

  Scenario: upstream non-2xx status passes through
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id" with status 206
    When I request GET "/users" with query "select=user_id" through the gateway as "editor"
    Then the response status is 206

  Scenario: an unmapped upstream route is a hard failure
    Given the scenario "users_grants"
    When I request GET "/users" with query "select=user_id" through the gateway as "editor"
    Then the response status is 500
    And the response error mentions "no route for GET /users?select=user_id"

  Scenario: blocked requests never reach PostgREST
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/users" with query "select=user_id&status=eq.active" through the gateway as "editor"
    Then the response status is 403
    And postgrest received no requests
