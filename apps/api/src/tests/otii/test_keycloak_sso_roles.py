"""Which Learn role a person gets from their otii roles at sign-in."""

from src.otii.keycloak_sso import ADMIN_ROLE_ID, LEARNER_ROLE_ID, ROLES_CLAIM, learn_role_for


def test_super_admins_are_learn_admins():
    assert learn_role_for({ROLES_CLAIM: ["default-roles-otii", "super_admin"]}) == ADMIN_ROLE_ID


def test_everyone_else_is_a_learner():
    assert learn_role_for({ROLES_CLAIM: ["admin"]}) == LEARNER_ROLE_ID
    assert learn_role_for({ROLES_CLAIM: ["manager", "staff"]}) == LEARNER_ROLE_ID
    assert learn_role_for({ROLES_CLAIM: []}) == LEARNER_ROLE_ID


def test_a_token_without_the_claim_gives_a_learner():
    assert learn_role_for({}) == LEARNER_ROLE_ID
    assert learn_role_for({ROLES_CLAIM: None}) == LEARNER_ROLE_ID


def test_a_single_string_claim_is_read_too():
    assert learn_role_for({ROLES_CLAIM: "super_admin"}) == ADMIN_ROLE_ID
    assert learn_role_for({ROLES_CLAIM: "staff"}) == LEARNER_ROLE_ID
