Feature: pgrmapper query syntax corner cases

  # Column-visibility checks look at parameter names and logic-tree
  # conditions, never at values: a value that happens to contain a hidden
  # column name must not be rejected.

  Scenario: filter value equal to a hidden column name
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /users?select=user_id&user_name=eq.status" returning JSON []
    When I request GET "/users" with query "select=user_id&user_name=eq.status" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&user_name=eq.status"

  Scenario: hidden names inside an in.(...) value
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /users?select=user_id&user_name=in.(status,manager)" returning JSON []
    When I request GET "/users" with query "select=user_id&user_name=in.(status,manager)" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&user_name=in.(status,manager)"

  Scenario: hidden name inside a quoted tree value with a dot
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /users?select=user_id&or=(user_name.eq."status.x",user_id.gt.1)" returning JSON []
    When I request GET "/users" with query "select=user_id&or=(user_name.eq."status.x",user_id.gt.1)" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&or=(user_name.eq."status.x",user_id.gt.1)"

  Scenario: quoted filter value with a comma and space
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /users?select=user_id&user_name=eq."Doe, John"" returning JSON []
    When I request GET "/users" with query "select=user_id&user_name=eq."Doe, John"" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&user_name=eq."Doe, John""

  Scenario: hidden name inside a like pattern
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /users?select=user_id&user_name=like.*status*" returning JSON []
    When I request GET "/users" with query "select=user_id&user_name=like.*status*" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&user_name=like.*status*"

  Scenario: JSON path subkey that matches a hidden column name
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /users?select=user_id&profile->>status=eq.active" returning JSON []
    When I request GET "/users" with query "select=user_id&profile->>status=eq.active" through the gateway as "manager"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&profile->>status=eq.active"

  Scenario: select alias containing a hidden name as a substring
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /users?select=status_alias:user_name" returning JSON []
    When I request GET "/users" with query "select=status_alias:user_name" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=status_alias:user_name"

  # Unknown parameter keys are the one token class treated as column
  # references, so they are rejected / stripped / forwarded per policy.

  Scenario: reject refuses an unknown parameter key
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/users" with query "select=user_id&cachebust=1" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.cachebust"
    And postgrest received no requests

  Scenario: enforce strips an unknown parameter key
    Given the scenario "users_grants"
    And the query policy is "enforce"
    And a postgrest response for "GET /users?select=user_id" returning JSON []
    When I request GET "/users" with query "select=user_id&cachebust=1" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id"

  Scenario: allow forwards an unknown parameter key
    Given the scenario "users_grants"
    And the query policy is "allow"
    And a postgrest response for "GET /users?select=user_id&cachebust=1" returning JSON []
    When I request GET "/users" with query "select=user_id&cachebust=1" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&cachebust=1"

  # Enforcement strips hidden projections while leaving values untouched.

  Scenario: enforce strips a hidden select column but keeps filter values
    Given the scenario "users_grants"
    And the query policy is "enforce"
    And a postgrest response for "GET /users?select=user_id&user_name=eq.status" returning JSON []
    When I request GET "/users" with query "select=user_id,status&user_name=eq.status" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&user_name=eq.status"
