"""Budget management UI (#462) and workspace usage dashboard (#447) tests."""

from datetime import UTC, datetime
from pathlib import Path

from app.extensions import db
from app.models import Budget, Project, UsageRecord, User, Workspace, WorkspaceMember
from app.services import budgets


def _workspace(owner, name="Team"):
    workspace = Workspace(user_id=owner.id, name=name)
    db.session.add(workspace)
    db.session.commit()
    return workspace


def _member(workspace, user, role):
    db.session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role=role,
            status="active",
        )
    )
    db.session.commit()


def _project(workspace, owner, name="api"):
    project = Project(
        workspace_id=workspace.id,
        user_id=owner.id,
        name=name,
        status="ready",
    )
    db.session.add(project)
    db.session.commit()
    return project


def _usage(
    user,
    workspace,
    *,
    tokens,
    cost,
    source="chat",
    project=None,
    status=None,
    created_at=None,
):
    record = UsageRecord(
        user_id=user.id,
        workspace_id=workspace.id,
        project_id=project.id if project else None,
        source=source,
        total_tokens=tokens,
        cost_estimate=cost,
        status=status,
        created_at=created_at or datetime.now(UTC),
    )
    db.session.add(record)
    db.session.commit()
    return record


def _team(app, db):
    owner = User(username="owner", email="owner@example.com")
    owner.set_password("supersecret123")
    contributor = User(username="contributor", email="contributor@example.com")
    contributor.set_password("supersecret123")
    viewer = User(username="viewer", email="viewer@example.com")
    viewer.set_password("supersecret123")
    outsider = User(username="outsider", email="outsider@example.com")
    outsider.set_password("supersecret123")
    db.session.add_all([owner, contributor, viewer, outsider])
    db.session.commit()

    workspace = _workspace(owner)
    _member(workspace, contributor, "contributor")
    _member(workspace, viewer, "viewer")
    project = _project(workspace, owner)

    _usage(owner, workspace, tokens=100, cost=0.10, project=project)
    _usage(
        contributor,
        workspace,
        tokens=50,
        cost=0.05,
        source="review",
        project=project,
    )
    _usage(contributor, workspace, tokens=10, cost=0.01)
    _usage(owner, workspace, tokens=999, cost=9.99, status="denied")
    other_workspace = _workspace(outsider, "Other")
    _usage(outsider, other_workspace, tokens=777, cost=7.77)

    return {
        "owner": owner,
        "contributor": contributor,
        "viewer": viewer,
        "outsider": outsider,
        "workspace": workspace,
        "project": project,
    }


class TestWorkspaceUsageDashboard:
    def test_owner_sees_totals_projects_sources_and_members(self, client, login, app, db):
        team = _team(app, db)
        login("owner@example.com")

        response = client.get(f"/workspaces/api/workspaces/{team['workspace'].id}/usage")
        assert response.status_code == 200
        body = response.get_json()
        assert body["role"] == "owner"
        assert body["member_detail"] == "all"
        assert body["totals"]["total_tokens"] == 160  # denied row excluded
        assert body["totals"]["count"] == 3
        assert body["totals"]["cost_estimate"] == 0.16

        projects = {row["name"]: row["totals"] for row in body["by_project"]}
        assert projects["api"]["total_tokens"] == 150
        assert projects["Unassigned"]["total_tokens"] == 10

        sources = {row["source"]: row["totals"] for row in body["by_source"]}
        assert sources["chat"]["total_tokens"] == 110
        assert sources["review"]["total_tokens"] == 50

        members = {row["username"]: row for row in body["by_member"]}
        assert set(members) == {"owner", "contributor"}
        assert members["owner"]["role"] == "owner"
        assert members["owner"]["totals"]["total_tokens"] == 100
        assert members["contributor"]["role"] == "contributor"
        assert members["contributor"]["totals"]["total_tokens"] == 60
        assert body["cost_is_estimate"] is True

    def test_contributor_only_sees_own_member_detail(self, client, login, app, db):
        team = _team(app, db)
        login("contributor@example.com")

        body = client.get(f"/workspaces/api/workspaces/{team['workspace'].id}/usage").get_json()
        assert body["role"] == "contributor"
        assert body["member_detail"] == "self"
        assert len(body["by_member"]) == 1
        assert body["by_member"][0]["username"] == "contributor"
        assert body["by_member"][0]["totals"]["total_tokens"] == 60
        # Aggregate workspace detail is still available to members.
        assert body["totals"]["total_tokens"] == 160
        assert len(body["by_project"]) == 2

    def test_viewer_can_read_aggregate_dashboard(self, client, login, app, db):
        team = _team(app, db)
        login("viewer@example.com")

        response = client.get(f"/workspaces/api/workspaces/{team['workspace'].id}/usage")
        assert response.status_code == 200
        assert response.get_json()["member_detail"] == "self"
        assert response.get_json()["by_member"] == []

    def test_non_member_gets_404(self, client, login, app, db):
        team = _team(app, db)
        login("outsider@example.com")
        response = client.get(f"/workspaces/api/workspaces/{team['workspace'].id}/usage")
        assert response.status_code == 404

    def test_anonymous_user_is_redirected(self, client, app, db):
        team = _team(app, db)
        response = client.get(f"/workspaces/api/workspaces/{team['workspace'].id}/usage")
        assert response.status_code == 302

    def test_members_page_contains_usage_dashboard(self, client, login, app, db):
        team = _team(app, db)
        login("contributor@example.com")
        response = client.get(f"/workspaces/{team['workspace'].id}/members")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Workspace usage" in html
        assert "workspace-usage-totals" in html
        assert "By project" in html
        assert "By source" in html
        # Members do not get the owner-only member table.
        assert 'id="workspace-usage-members-wrap" hidden' in html


class TestBudgetManagementUi:
    def test_owner_page_shows_usage_against_limits_and_controls(self, client, login, app, db):
        team = _team(app, db)
        budgets.upsert_budget(
            scope="workspace",
            scope_id=team["workspace"].id,
            token_limit=1000,
            cost_limit=1.0,
            warn_threshold=0.5,
        )
        login("owner@example.com")

        response = client.get("/billing/")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Budgets &amp; quotas" in html or "Budgets & quotas" in html
        assert 'id="budget-form"' in html
        assert 'data-scope="workspace"' in html
        assert f'data-scope-id="{team["workspace"].id}"' in html
        assert 'data-token-limit="1000"' in html
        assert 'data-cost-limit="1.0"' in html
        assert "estimated" in html
        assert "usage-bar" in html
        assert team["workspace"].name in html

    def test_owner_can_create_and_delete_budget_from_ui_api(self, client, login, app, db):
        team = _team(app, db)
        login("owner@example.com")

        created = client.put(
            "/billing/api/budgets",
            json={
                "scope": "workspace",
                "workspace_id": team["workspace"].id,
                "token_limit": 500,
                "cost_limit": 2.0,
                "warn_threshold": 0.75,
            },
        )
        assert created.status_code == 200
        body = created.get_json()["budget"]
        assert body["scope_name"] == team["workspace"].name
        assert body["tokens_used"] == 160
        assert body["cost_is_estimate"] is True

        deleted = client.delete(f"/billing/api/budgets/{body['id']}")
        assert deleted.status_code == 200
        assert db.session.get(Budget, body["id"]) is None

    def test_contributor_cannot_manage_workspace_budget(self, client, login, app, db):
        team = _team(app, db)
        login("contributor@example.com")
        response = client.put(
            "/billing/api/budgets",
            json={
                "scope": "workspace",
                "workspace_id": team["workspace"].id,
                "token_limit": 10,
            },
        )
        assert response.status_code == 403

    def test_global_admin_can_manage_another_users_workspace(self, client, login, app, db):
        team = _team(app, db)
        admin = User(
            username="admin",
            email="admin@example.com",
            is_admin=True,
        )
        admin.set_password("supersecret123")
        db.session.add(admin)
        db.session.commit()
        login("admin@example.com")

        listing = client.get("/billing/api/budgets").get_json()
        assert any(workspace["id"] == team["workspace"].id for workspace in listing["workspaces"])

        created = client.put(
            "/billing/api/budgets",
            json={
                "scope": "workspace",
                "workspace_id": team["workspace"].id,
                "token_limit": 123,
            },
        )
        assert created.status_code == 200
        budget = created.get_json()["budget"]
        assert budget["scope_name"] == team["workspace"].name

        page = client.get("/billing/")
        assert page.status_code == 200
        assert team["workspace"].name in page.get_data(as_text=True)

        assert client.delete(f"/billing/api/budgets/{budget['id']}").status_code == 200

    def test_non_admin_cannot_manage_unowned_workspace(self, client, login, app, db):
        team = _team(app, db)
        login("outsider@example.com")
        response = client.put(
            "/billing/api/budgets",
            json={
                "scope": "workspace",
                "workspace_id": team["workspace"].id,
                "token_limit": 10,
            },
        )
        assert response.status_code == 404

    def test_user_budget_still_self_service(self, client, login, app, db):
        _team(app, db)
        login("contributor@example.com")
        response = client.put(
            "/billing/api/budgets",
            json={"scope": "user", "token_limit": 42},
        )
        assert response.status_code == 200
        assert response.get_json()["budget"]["scope_name"] == "My account"


class TestUsageDashboardStates:
    def test_workspace_usage_has_loading_empty_and_retryable_error_states(
        self, client, login, app, db
    ):
        team = _team(app, db)
        login("contributor@example.com")

        html = client.get(f"/workspaces/{team['workspace'].id}/members").get_data(as_text=True)
        assert "Loading usage..." in html

        js = (Path(app.root_path).parent / "app" / "static" / "js" / "members.js").read_text(
            encoding="utf-8"
        )
        assert "No usage recorded in this workspace yet." in js
        assert "Usage could not be loaded." in js
        assert "usage-retry" in js
        assert "Retry" in js

    def test_billing_usage_page_has_an_explicit_empty_state(self, client, login, make_user):
        make_user(username="empty-usage", email="empty-usage@example.com")
        login("empty-usage@example.com")

        html = client.get("/billing/usage").get_data(as_text=True)
        assert "No usage in this window." in html
