.PHONY: demo eval report all clean check

# Everything here runs on a bare python3. No virtualenv, no pip install.
# That is deliberate: a live demo must not depend on a dependency resolver.

demo:            ## walk one incident end to end
	python3 agents/demo_inference.py --incident INC-001

demo-bypass:     ## the deterministic path, zero model calls
	python3 agents/demo_inference.py --incident INC-005

demo-miss:       ## a known misclassification, contained by guardrails
	python3 agents/demo_inference.py --incident INC-009

eval:            ## score both engines against the golden set
	python3 evals/harness/run_eval.py --engine all

report: eval     ## regenerate evals/results/RESULTS.md from the eval output
	python3 evals/harness/render_results.py

check:           ## validate the artifacts that other code depends on
	python3 -c "import json;json.load(open('contracts/schemas/rca.schema.json'));print('rca schema ok')"
	python3 -c "import json;json.load(open('dashboards/agent_operations.json'));print('dashboard ok')"
	python3 -c "import json;[json.loads(l) for l in open('evals/datasets/golden_incidents.jsonl') if l.strip()];print('golden set ok')"

all: check report demo

clean:
	rm -f evals/results/eval_run_*.json evals/results/per_incident.csv
