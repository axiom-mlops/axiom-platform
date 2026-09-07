import http from 'k6/http';
import { sleep } from 'k6';

export const options = {
  noConnectionReuse: true,   // each request re-resolves the Service, so new pods actually receive traffic
  scenarios: {
    inject: {
      executor: 'ramping-vus', startVUs: 5,
      stages: [
        { duration: '1m', target: 30 },
        { duration: '8m', target: 30 },   // longer hold so recovery has room after scale-out
        { duration: '1m', target: 5 },
      ],
      exec: 'heavy',
    },
  },
};

const BASE = 'http://mempressure.boutique.svc.cluster.local:8080';

export function heavy() {
  http.get(`${BASE}/heavy?hold_mb=14&secs=2`);
  sleep(0.2);
}
