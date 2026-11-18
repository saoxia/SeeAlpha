"""Run selected SeeAlpha services from the same source tree.

Usage: python -m node --role platform|agent|all
"""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="SeeAlpha service role")
    parser.add_argument("--role", choices=("platform", "agent", "all"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.role == "agent":
        from .agent import run_agent
        run_agent()
        return
    if args.role == "all":
        os.environ.setdefault("ENGINE_DEPLOYMENT", "docker")
    import uvicorn
    uvicorn.run("api.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
