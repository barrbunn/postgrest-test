Feature: pgrmapper query policy

  # --- reject (default): nothing is forwarded --------------------------------

  Scenario: reject refuses a filter on a hidden column
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/users" with query "select=user_id&status=eq.active" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses a select of a hidden column
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/users" with query "select=user_id,status" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses order on a hidden column
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/users" with query "select=user_id&order=status.desc" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses a hidden column inside an or tree
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/users" with query "select=user_id&or=(user_id.gt.1,status.eq.active)" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses a hidden column inside a not.and tree
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/users" with query "select=user_id&not.and=(status.eq.active,user_id.gt.1)" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses a hidden embedded column
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/grants" with query "select=grant_id,users(status)" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses an embed-qualified filter
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/grants" with query "select=grant_id&users.status=eq.active" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses an embed-qualified order
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/grants" with query "select=grant_id&users.order=status.desc" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses an aliased embed column
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/grants" with query "select=grant_id,author:users(status)" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.status"
    And postgrest received no requests

  Scenario: reject refuses a JSON path on a hidden column
    Given the scenario "users_grants"
    And the query policy is "reject"
    When I request GET "/users" with query "select=user_id&profile->>level=eq.admin" through the gateway as "editor"
    Then the response status is 403
    And the response error mentions "users.profile"
    And postgrest received no requests

  Scenario: reject expands an embed wildcard to visible columns
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /grants?select=grant_id,users(user_id,user_name)" returning JSON [{"grant_id": 1}]
    When I request GET "/grants" with query "select=grant_id,users(*)" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /grants?select=grant_id,users(user_id,user_name)"

  Scenario: reject expands an aliased embed wildcard
    Given the scenario "users_grants"
    And the query policy is "reject"
    And a postgrest response for "GET /grants?select=grant_id,author:users(user_id,user_name)" returning JSON [{"grant_id": 1}]
    When I request GET "/grants" with query "select=grant_id,author:users(*)" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /grants?select=grant_id,author:users(user_id,user_name)"

  # --- enforce: strip the offending parts -------------------------------------

  Scenario: enforce strips hidden parts from select, filter and order
    Given the scenario "users_grants"
    And the query policy is "enforce"
    And a postgrest response for "GET /users?select=user_id&limit=2" returning JSON [{"user_id": 1}]
    When I request GET "/users" with query "select=user_id,status&status=eq.active&order=status.desc&limit=2" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&limit=2"

  Scenario: enforce drops an embed with no visible columns
    Given the scenario "users_grants"
    And the query policy is "enforce"
    And a postgrest response for "GET /grants?select=grant_id&limit=1" returning JSON [{"grant_id": 1}]
    When I request GET "/grants" with query "select=grant_id,users(status)&limit=1" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /grants?select=grant_id&limit=1"

  Scenario: enforce strips hidden conditions from a tree
    Given the scenario "users_grants"
    And the query policy is "enforce"
    And a postgrest response for "GET /users?select=user_id&or=(user_id.gt.1)" returning JSON [{"user_id": 2}]
    When I request GET "/users" with query "select=user_id&or=(user_id.gt.1,status.eq.active)" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&or=(user_id.gt.1)"

  Scenario: enforce strips hidden embed-qualified filters
    Given the scenario "users_grants"
    And the query policy is "enforce"
    And a postgrest response for "GET /grants?select=grant_id,users(user_id,user_name)" returning JSON [{"grant_id": 1}]
    When I request GET "/grants" with query "select=grant_id,users(*)&users.status=eq.active" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /grants?select=grant_id,users(user_id,user_name)"

  Scenario: enforce keeps only visible order terms
    Given the scenario "users_grants"
    And the query policy is "enforce"
    And a postgrest response for "GET /users?select=user_id&order=user_id.asc" returning JSON [{"user_id": 1}]
    When I request GET "/users" with query "select=user_id&order=status.desc,user_id.asc" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&order=user_id.asc"

  # --- allow: legacy pass-through ---------------------------------------------

  Scenario: allow forwards a hidden filter
    Given the scenario "users_grants"
    And the query policy is "allow"
    And a postgrest response for "GET /users?select=user_id&status=eq.active" returning JSON [{"user_id": 1}]
    When I request GET "/users" with query "select=user_id&status=eq.active" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&status=eq.active"

  Scenario: allow forwards a hidden embedded column
    Given the scenario "users_grants"
    And the query policy is "allow"
    And a postgrest response for "GET /grants?select=grant_id,users(status)" returning JSON [{"grant_id": 1, "users": {"status": "active"}}]
    When I request GET "/grants" with query "select=grant_id,users(status)" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /grants?select=grant_id,users(status)"

  Scenario: allow forwards order on a hidden column
    Given the scenario "users_grants"
    And the query policy is "allow"
    And a postgrest response for "GET /users?select=user_id&order=status.desc" returning JSON [{"user_id": 1}]
    When I request GET "/users" with query "select=user_id&order=status.desc" through the gateway as "editor"
    Then the response status is 200
    And postgrest received the last request "GET /users?select=user_id&order=status.desc"
