import http from 'k6/http';
import { sleep } from 'k6';

export const options = {
  scenarios: {
    baseline: {                                  // Phase 1: healthy, so the panel sees "before"
      executor: 'constant-vus', vus: 5, duration: '2m', exec: 'light',
    },
    inject: {                                    // Phase 2: memory climb on a timer, starts at 2m
      executor: 'ramping-vus', startTime: '2m', startVUs: 5,
      stages: [
        { duration: '1m', target: 30 },          // per-pod memory starts climbing
        { duration: '4m', target: 30 },          // sustained; agent detects and remediates in here
        { duration: '1m', target: 5 },           // ramp down; recovery
      ],
      exec: 'heavy',
    },
  },
};

const BASE = 'http://localhost:18080';            // via kubectl port-forward

export function light() {
  http.get(`${BASE}/healthz`);
  sleep(1);
}

export function heavy() {
  http.get(`${BASE}/heavy?hold_mb=18&secs=2`);
  sleep(0.2);
}
