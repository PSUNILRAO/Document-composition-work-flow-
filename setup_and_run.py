"""
setup_and_run.py — Run this first.

    python setup_and_run.py           # interactive menu
    python setup_and_run.py --web     # web UI at localhost:5000
    python setup_and_run.py --batch bank_statement --all
    python setup_and_run.py --test    # regression tests
    python setup_and_run.py --approve bank_statement
"""
import sys, os, subprocess, argparse

def setup():
    for d in ["templates","data","output","tests/golden","logs","uploads"]:
        os.makedirs(d, exist_ok=True)
    subprocess.run([sys.executable, "create_sample_data.py"], check=True)
    subprocess.run([sys.executable, "create_templates.py"],   check=True)
    print("\n✅  Setup complete.\n")

def menu():
    print("\n" + "═"*60)
    print("  BRD → Template → Engine → PDF  (No OpenText)")
    print("═"*60)
    print("  [1]  Setup + Web UI         (http://localhost:5000)")
    print("  [2]  Setup + Batch all types")
    print("  [3]  Setup only")
    print("  [4]  Web UI (skip setup)")
    print("  [5]  Run regression tests")
    print("  [6]  Approve golden snapshots")
    print("  [q]  Quit")
    c = input("\n  Choice: ").strip().lower()
    if   c == "1": setup(); subprocess.run([sys.executable,"app_web.py"])
    elif c == "2": setup(); subprocess.run([sys.executable,"batch_runner.py","--all-types"])
    elif c == "3": setup()
    elif c == "4": subprocess.run([sys.executable,"app_web.py"])
    elif c == "5": subprocess.run([sys.executable,"validator.py","--test"])
    elif c == "6":
        dt = input("  Doc type: ").strip()
        subprocess.run([sys.executable,"validator.py","--approve",dt])
    elif c in ("q","quit"): sys.exit(0)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--web",    action="store_true")
    p.add_argument("--test",   action="store_true")
    p.add_argument("--batch",  metavar="DOC_TYPE")
    p.add_argument("--all",    action="store_true")
    p.add_argument("--approve",metavar="DOC_TYPE")
    p.add_argument("--setup",  action="store_true")
    args = p.parse_args()

    if   args.setup:   setup()
    elif args.web:     setup(); subprocess.run([sys.executable,"app_web.py"])
    elif args.test:    subprocess.run([sys.executable,"validator.py","--test"])
    elif args.approve: subprocess.run([sys.executable,"validator.py","--approve",args.approve])
    elif args.batch:
        cmd = [sys.executable,"batch_runner.py","--type",args.batch]
        if args.all: cmd.append("--all")
        subprocess.run(cmd)
    else:
        menu()
