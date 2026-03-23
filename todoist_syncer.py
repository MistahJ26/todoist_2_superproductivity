import argparse
import json
import random
import string
from datetime import datetime

from todoist_api_python.api import TodoistAPI  # synchronous client


# ----------------------------------------------------------------------
# Helper: fetch all Todoist tasks (materialise the paginator)
# ----------------------------------------------------------------------
def get_todoist_tasks(token: str):
    api = TodoistAPI(token)
    try:
        pages = api.get_tasks()          # Returns a ResultsPaginator (iterable of lists)
        tasks = []
        for page in pages:               # each page = list of Task objects
            tasks.extend(page)
        return tasks    
    except Exception as exc:
        raise SystemExit(f"Error fetching Todoist tasks: {exc}") from exc


# ----------------------------------------------------------------------
# Helper: fetch all Todoist projects (id → name)
# ----------------------------------------------------------------------
def get_todoist_projects(token: str):
    api = TodoistAPI(token)
    try:
        pages = api.get_projects()
        proj_map = {}
        for page in pages:
            for p in page:
                proj_map[p.id] = p.name
        return proj_map    
    except Exception as exc:
        raise SystemExit(f"Error fetching Todoist projects: {exc}") from exc


# ----------------------------------------------------------------------
# Helper: fetch comments for a single task (returns list of strings)
# ----------------------------------------------------------------------
def get_todoist_comments(api: TodoistAPI, task_id: str):
    try:
        # get_comments also returns a ResultsPaginator
        pages = api.get_comments(task_id)
        comments = []
        for page in pages:
            for c in page:
                # Comment objects have a `content` field
                comments.append(getattr(c, "content", ""))
        return comments
    except Exception:
        # If comments cannot be retrieved we simply return an empty list
        return []


# ----------------------------------------------------------------------
# Vault helpers
# ----------------------------------------------------------------------
def load_vault(vault_path: str):
    try:
        with open(vault_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"Vault file not found: {vault_path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in vault: {exc}") from exc


def get_existing_task_ids(vault_data):
    return set(vault_data.get("data", {}).get("task", {}).get("ids", []))


def get_existing_project_ids(vault_data):
    return set(vault_data.get("data", {}).get("project", {}).get("ids", []))


def generate_task_id(existing_ids):
    while True:
        new_id = "".join(random.choices(string.ascii_uppercase, k=21))
        if new_id not in existing_ids:
            return new_id


def get_or_create_sp_project(vault_data, proj_name):
    """
    Return the SP project id for `proj_name`.
    If it does not exist, create a new project entity (using an existing one as a template)
    and return its id.
    """
    proj_entities = vault_data["data"]["project"]["entities"]
    proj_ids = vault_data["data"]["project"]["ids"]

    # 1️⃣ Look for existing project with same title
    for pid, ent in proj_entities.items():
        if ent.get("title") == proj_name:
            return pid

    # 2️⃣ No match → create a new project
    template = next(iter(proj_entities.values())) if proj_entities else {}
    new_id = generate_task_id(set(proj_ids))

    new_proj = template.copy() if template else {}
    new_proj.update(
        {
            "id": new_id,
            "title": proj_name,
            # keep template colour or fall back to a pleasant default
            "color": proj_entities.get(next(iter(proj_entities)), {}).get("color", "#56CCF2")
            if proj_entities
            else "#56CCF2",
            "isFavorite": False,
            "archived": False,
            "childOrder": [],
            "collapsed": False,
            "sortOrder": 0,
            "created": int(datetime.now().timestamp() * 1000),
            "updated": int(datetime.now().timestamp() * 1000),
        }
    )
    proj_entities[new_id] = new_proj
    proj_ids.append(new_id)
    return new_id


def add_task_entity(vault_data, title, project_id, notes="", attachments=None):
    """
    Create a SP task entity and insert it into the vault.
    Returns the new SP task id and the mutable entity dict (for later wiring).
    """
    if attachments is None:
        attachments = []

    task_entities = vault_data["data"]["task"]["entities"]
    task_ids = vault_data["data"]["task"]["ids"]

    new_id = generate_task_id(set(task_ids))

    task_entity = {
        "id": new_id,
        "projectId": project_id,                     # None → Inbox, otherwise project id
        "subTaskIds": [],
        "timeSpentOnDay": {},
        "timeSpent": 0,
        "timeEstimate": 3600000,                     # 1 h in ms (adjust if needed)
        "isDone": False,
        "doneOn": None,
        "title": title,
        "notes": notes,
        "tagIds": [],                                # no automatic tag
        "parentId": None,
        "reminderId": None,
        "created": int(datetime.now().timestamp() * 1000),
        "repeatCfgId": None,
        "plannedAt": None,
        "_showSubTasksMode": 2,
        "attachments": attachments,                  # list of dicts (see below)
        "issueId": None,
        "issuePoints": None,
        "issueType": None,
        "issueAttachmentNr": None,
        "attacissueLastUpdatedhments": None,
        "issueWasUpdated": None,
    }

    task_entities[new_id] = task_entity
    task_ids.append(new_id)
    return new_id, task_entity


# ----------------------------------------------------------------------
# Main routine
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Sync Todoist tasks (with subtasks) into Super Productivity, "
                    "creating missing projects and copying notes/attachments/comments."
    )
    parser.add_argument("--token", required=True, help="Todoist API token")
    parser.add_argument(
        "--path",
        required=True,
        help="Directory containing vault.json (will read vault.json and write synced_vault.json)",
    )
    parser.add_argument(
        "--subtasks",
        type=lambda x: str(x).lower() in ["true", "t", "y", "yes"],
        default=False,
        help="Kept for compatibility – hierarchy is always built.",
    )
    args = parser.parse_args()

    # --------------------------------------------------------------
    # 1️⃣ Pull data from Todoist
    # --------------------------------------------------------------
    todoist_tasks = get_todoist_tasks(args.token)
    print(f"🔎 Fetched {len(todoist_tasks)} tasks from Todoist")

    todoist_projects = get_todoist_projects(args.token)
    print(f"🔎 Fetched {len(todoist_projects)} projects from Todoist")

    api = TodoistAPI(args.token)  # reuse for comment fetching

    # --------------------------------------------------------------
    # 2️⃣ Load the SP vault
    # --------------------------------------------------------------
    vault_file = f"{args.path.rstrip('/')}/vault.json"
    vault_data = load_vault(vault_file)

    existing_task_ids = get_existing_task_ids(vault_data)
    existing_project_ids = get_existing_project_ids(vault_data)

    # --------------------------------------------------------------
    # 3️⃣ Map Todoist project → SP project (create if missing)
    # --------------------------------------------------------------
    td_proj_to_sp_proj = {}
    for td_proj_id, td_proj_name in todoist_projects.items():
        # If a Todoist task has no project (Inbox) we treat it as a project named "Inbox"
        proj_name = td_proj_name if td_proj_name is not None else "Inbox"
        sp_proj_id = get_or_create_sp_project(vault_data, proj_name)
        td_proj_to_sp_proj[td_proj_id] = sp_proj_id
        print(f"🔗 Mapped Todoist project '{proj_name}' → SP project id {sp_proj_id}")

    # --------------------------------------------------------------
    # 4️⃣ First pass – create task entities (no parent/child wiring yet)
    # --------------------------------------------------------------
    td_id_to_sp_id = {}          # Todoist task id → SP task id
    td_id_to_task_entity = {}    # Todoist task id → mutable SP task entity

    for t in todoist_tasks:
        # Skip if we already processed this Todoist id (protects against re‑runs)
        if t.id in td_id_to_sp_id:
            continue

        # Determine SP project id (None for Inbox)
        sp_project_id = None
        if t.project_id is not None:
            sp_project_id = td_proj_to_sp_proj.get(t.project_id)
        else:
            # No project in Todoist → treat as Inbox project
            sp_project_id = td_proj_to_sp_proj.get(None)  # will be created as "Inbox" above

        # Gather notes: description + comments
        notes = getattr(t, "description", "") or ""
        comments = get_todoist_comments(api, t.id)
        if comments:
            notes = (notes + "\n\n---\n\n" + "\n\n".join(comments)).strip()

        # Build attachments list (SP expects a list of dicts; we keep fileName & fileUrl)
        attachments = []
        for att in getattr(t, "attachments", []) or []:
            # Todoist attachment objects have file_name and file_url (may also have resource_type)
            file_name = getattr(att, "file_name", "")
            file_url = getattr(att, "file_url", "")
            if file_name or file_url:
                attachments.append({"fileName": file_name, "fileUrl": file_url})

        # Create the SP task entity        
        sp_task_id, task_entity = add_task_entity(
            vault_data,
            title=t.content,
            project_id=sp_project_id,
            notes=notes,
            attachments=attachments,
        )

        td_id_to_sp_id[t.id] = sp_task_id
        td_id_to_task_entity[t.id] = task_entity

    print(f"✅ Created {len(td_id_to_sp_id)} task entities in the vault")

    # --------------------------------------------------------------
    # 5️⃣ Second pass – wire parent/child relationships (subtasks)
    # --------------------------------------------------------------
    for t in todoist_tasks:
        if t.parent_id is None:
            continue  # top‑level task, parentId stays None

        child_sp_id = td_id_to_sp_id.get(t.id)
        parent_sp_id = td_id_to_sp_id.get(t.parent_id)

        if child_sp_id is None or parent_sp_id is None:
            print(
                f"⚠️  Could not wire subtask '{t.content}' (Todoist id {t.id}) "
                f"to parent (Todoist id {t.parent_id}). Skipping."
            )
            continue

        child_entity = td_id_to_task_entity[t.id]
        parent_entity = td_id_to_task_entity[t.parent_id]

        # Set child's parentId
        child_entity["parentId"] = parent_sp_id
        # Add child's id to parent's subTaskIds list (avoid duplicates)
        if child_sp_id not in parent_entity["subTaskIds"]:
            parent_entity["subTaskIds"].append(child_sp_id)

    print("🔗 Parent/child relationships wired")

    # --------------------------------------------------------------
    # 6️⃣ Write the synchronized vault
    # --------------------------------------------------------------
    output_file = f"{args.path.rstrip('/')}/synced_vault.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(vault_data, f, indent=2, ensure_ascii=False)

    print(f"🎉 Sync complete. Updated vault written to: {output_file}")


if __name__ == "__main__":
    main()
