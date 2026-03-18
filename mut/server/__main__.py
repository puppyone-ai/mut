"""Server CLI: python -m mut.server <command>

Commands:
    init <path> [--name NAME]
        Initialize a server repository

    add-scope <path> --id ID --scope-path PATH [--exclude /x/,/y/]
        Define a subtree boundary (no auth — just geometry)

    issue-credential <path> --scope SCOPE_ID --agent AGENT_ID [--mode rw]
        Issue an API key for an agent to access a scope

    serve <path> [--host HOST] [--port PORT] [--auth none|api_key]
        Start the HTTP server (default auth: none)
"""

import argparse
import json
import sys
from pathlib import Path

from mut.server.repo import ServerRepo
from mut.server.server import serve


def cmd_init(args):
    try:
        repo = ServerRepo.init(args.path, args.name)
        print(f"Initialized Mut server repo at {repo.root}")
    except FileExistsError as e:
        print(f"fatal: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_add_scope(args):
    repo = ServerRepo(args.path)
    repo.check_init()
    exclude = ([e.strip() for e in args.exclude.split(",")]
               if args.exclude else [])
    scope = repo.add_scope(args.id, args.scope_path, exclude)
    print(f"Added scope '{scope['id']}': path={scope['path']}")
    if exclude:
        print(f"  exclude: {exclude}")


def cmd_issue_credential(args):
    """Issue an API key credential for an agent+scope."""
    repo = ServerRepo(args.path)
    repo.check_init()

    scope = repo.scopes.get_by_id(args.scope)
    if scope is None:
        print(f"fatal: scope '{args.scope}' not found", file=sys.stderr)
        sys.exit(1)

    from mut.server.auth.api_key import ApiKeyAuth
    credentials_file = Path(args.path).resolve() / "credentials.json"
    auth = ApiKeyAuth(repo.scopes, credentials_file)
    key = auth.issue(args.agent, args.scope, args.mode)

    print(key)
    print(f"  agent: {args.agent}", file=sys.stderr)
    print(f"  scope: {scope['path']} ({args.mode})", file=sys.stderr)


def cmd_serve(args):
    repo = ServerRepo(args.path)
    repo.check_init()

    if args.auth == "api_key":
        from mut.server.auth.api_key import ApiKeyAuth
        credentials_file = Path(args.path).resolve() / "credentials.json"
        authenticator = ApiKeyAuth(repo.scopes, credentials_file)
    else:
        from mut.server.auth.no_auth import NoAuth
        authenticator = NoAuth(repo.scopes)

    serve(args.path, args.host, args.port, authenticator)


def main():
    parser = argparse.ArgumentParser(
        prog="mut-server",
        description="Mut server management CLI",
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Initialize a server repository")
    p_init.add_argument("path", help="Directory for the server repo")
    p_init.add_argument("--name", default="my-project", help="Project name")

    p_scope = sub.add_parser("add-scope",
                             help="Add a scope (subtree boundary)")
    p_scope.add_argument("path", help="Server repo directory")
    p_scope.add_argument("--id", required=True, help="Scope ID")
    p_scope.add_argument("--scope-path", required=True,
                         help="Path prefix, e.g. /src/")
    p_scope.add_argument("--exclude", default="",
                         help="Comma-separated excluded paths")

    p_cred = sub.add_parser("issue-credential",
                            help="Issue an API key for an agent")
    p_cred.add_argument("path", help="Server repo directory")
    p_cred.add_argument("--scope", required=True, help="Scope ID")
    p_cred.add_argument("--agent", required=True, help="Agent ID")
    p_cred.add_argument("--mode", default="rw", help="r or rw (default: rw)")

    p_serve = sub.add_parser("serve", help="Start the HTTP server")
    p_serve.add_argument("path", help="Server repo directory")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=9742)
    p_serve.add_argument("--auth", default="none",
                         choices=["none", "api_key"],
                         help="Auth mode (default: none)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "init": cmd_init,
        "add-scope": cmd_add_scope,
        "issue-credential": cmd_issue_credential,
        "serve": cmd_serve,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
