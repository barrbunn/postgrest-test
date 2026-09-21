Feature: pgrmapper legacy (HS256) identity

  Scenario: HS256 role drives the projection
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id,user_name" returning JSON []
    When I request GET "/users" with query "select=*" directly with HS256 role "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id,user_name"

  Scenario: anonymous requests have no rule and are forwarded unfiltered
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id,status" returning JSON []
    When I request GET "/users" with query "select=user_id,status" directly without a token
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id,status"

  Scenario: invalid HS256 token falls back to anon
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id,status" returning JSON []
    When I request GET "/users" with query "select=user_id,status" directly with an invalid HS256 token
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id,status"

  Scenario: expired HS256 token falls back to anon
    Given the scenario "users_grants"
    And a postgrest response for "GET /users?select=user_id,status" returning JSON []
    When I request GET "/users" with query "select=user_id,status" directly with an expired HS256 token
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id,status"

  Scenario: viewer is blocked by an empty visible rule
    Given the scenario "users_grants"
    When I request GET "/users" with query "select=*" directly with HS256 role "viewer"
    Then the response status is 403
    And the response error mentions "no visible columns"
    And postgrest received no requests
