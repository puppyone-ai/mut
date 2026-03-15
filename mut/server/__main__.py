"""Server CLI: python -m mut.server <command>

Commands:
    init <path> [--name NAME]        Initialize a server repository
    add-scope <path> --id ID --scope-path PATH --agents A1,A2 [--mode rw] [--exclude /x/,/y/]
    issue-token <path> --agent AGENT_ID
    serve <path> [--host HOST] [--port PORT]
"""

import argparse
import sys

from mut.server.repo import ServerRepo
from mut.server.server import serve


def cmd_init(args):
    try:
        repo = ServerRepo.init(args.path, args.name)
        print(f"Initialized Mut server repo at {repo.root}")
        print(f"  secret key: {repo.meta / 'secret.key'}")
    except FileExistsError as e:
        print(f"fatal: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_add_scope(args):
    repo = ServerRepo(args.path)
    repo.check_init()
    agents = [a.strip() for a in args.agents.split(",")]
    exclude = [e.strip() for e in args.exclude.split(",")] if args.exclude else []
    scope = repo.add_scope(args.id, args.scope_path, agents, args.mode, exclude)
    print(f"Added scope '{scope['id']}': path={scope['path']} agents={scope['agents']} mode={scope['mode']}")
    if exclude:
        print(f"  exclude: {exclude}")


def cmd_create_invite(args):
    repo = ServerRepo(args.path)
    repo.check_init()
    exclude = [e.strip() for e in args.exclude.split(",")] if args.exclude else []
    invite = repo.create_invite(args.scope_path, args.mode, exclude, args.max_uses)
    host = args.host or "localhost"
    port = args.port or 9742
    url = f"http://{host}:{port}/invite/{invite['id']}"
    print(f"Invite created:")
    print(f"  scope: {invite['scope_path']} ({invite['mode']})")
    print(f"  max uses: {invite['max_uses'] or 'unlimited'}")
    print(f"")
    print(f"  {url}")
    print(f"")
    print(f"Share this URL. Agents can register with:")
    print(f"  mut register {url}")


def cmd_issue_token(args):
    repo = ServerRepo(args.path)
    repo.check_init()
    try:
        token = repo.issue_token(args.agent, args.expiry)
        print(token)
    except ValueError as e:
        print(f"fatal: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_serve(args):
    """Start the async HTTP server."""
    serve(args.path, args.host, args.port)


def main():
    parser = argparse.ArgumentParser(
        prog="mut-server",
        description="Mut server management CLI",
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Initialize a server repository")
    p_init.add_argument("path", help="Directory for the server repo")
    p_init.add_argument("--name", default="my-project", help="Project name")

    p_scope = sub.add_parser("add-scope", help="Add a scope (permission boundary)")
    p_scope.add_argument("path", help="Server repo directory")
    p_scope.add_argument("--id", required=True, help="Scope ID")
    p_scope.add_argument("--scope-path", required=True, help="Path prefix, e.g. /src/")
    p_scope.add_argument("--agents", required=True, help="Comma-separated agent IDs")
    p_scope.add_argument("--mode", default="rw", help="r or rw (default: rw)")
    p_scope.add_argument("--exclude", default="", help="Comma-separated excluded paths")

    p_invite = sub.add_parser("create-invite", help="Create an invite URL for agents")
    p_invite.add_argument("path", help="Server repo directory")
    p_invite.add_argument("--scope-path", required=True, help="Path prefix, e.g. /src/")
    p_invite.add_argument("--mode", default="rw", help="r or rw (default: rw)")
    p_invite.add_argument("--exclude", default="", help="Comma-separated excluded paths")
    p_invite.add_argument("--max-uses", type=int, default=0,
                          help="Max registrations (0=unlimited)")
    p_invite.add_argument("--host", default="", help="Server hostname for URL")
    p_invite.add_argument("--port", type=int, default=0, help="Server port for URL")

    p_token = sub.add_parser("issue-token", help="Issue a token for an agent")
    p_token.add_argument("path", help="Server repo directory")
    p_token.add_argument("--agent", required=True, help="Agent ID")
    p_token.add_argument("--expiry", type=int, default=0, help="Token expiry in seconds (0=never)")

    p_serve = sub.add_parser("serve", help="Start the HTTP server")
    p_serve.add_argument("path", help="Server repo directory")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=9742)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "init": cmd_init,
        "add-scope": cmd_add_scope,
        "create-invite": cmd_create_invite,
        "issue-token": cmd_issue_token,
        "serve": cmd_serve,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
