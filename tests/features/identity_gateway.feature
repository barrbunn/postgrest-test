Feature: pgrmapper gateway-mode identity chain

  Scenario: valid chain is accepted
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id,user_name" returning JSON []
    When I request GET "/users" with query "select=user_id,user_name" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id,user_name"

  Scenario: missing user JWT is rejected
    Given the scenario "users_grants"
    When I request GET "/users" through the gateway without a token
    Then the response status is 401
    And the response error mentions "missing user JWT"

  Scenario: expired user JWT is rejected
    Given the scenario "users_grants"
    When I request GET "/users" through the gateway as "editor" with an expired user JWT
    Then the response status is 401
    And the response error mentions "user JWT expired"

  Scenario: user JWT for another audience is rejected
    Given the scenario "users_grants"
    When I request GET "/users" through the gateway as "editor" with a user JWT for audience "someone-else"
    Then the response status is 401
    And the response error mentions "unexpected audience"

  Scenario: tampered user JWT is rejected
    Given the scenario "users_grants"
    When I request GET "/users" through the gateway as "editor" with a tampered user JWT
    Then the response status is 401
    And the response error mentions "invalid user JWT"

  Scenario: invalid service JWT is rejected before the role lookup
    Given the scenario "users_grants"
    When I request GET "/users" directly with an invalid service JWT as "editor"
    Then the response status is 401
    And the response error mentions "invalid service JWT"
    And postgrest received no requests

  Scenario: expired service JWT is rejected
    Given the scenario "users_grants"
    When I request GET "/users" directly with an expired service JWT as "editor"
    Then the response status is 401
    And the response error mentions "invalid service JWT"

  Scenario: service JWT without embedded user JWT is rejected
    Given the scenario "users_grants"
    When I request GET "/users" directly with a service JWT without embedded user JWT as "editor"
    Then the response status is 401
    And the response error mentions "invalid embedded user JWT"

  Scenario: the role header is trusted after the chain is verified
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id,user_name,status" returning JSON []
    When I request GET "/users" with query "select=user_id,user_name,status" directly with role header "manager" and user JWT role "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id,user_name,status"
