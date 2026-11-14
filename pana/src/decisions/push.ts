// The push gate (PSH-06..PSH-09): one `noul` question over a rendered delivery, asked before the
// user's phone is buzzed.
//
// Every delivery to the user used to push: a session's one-line status report and a question only
// Josh can answer both interrupted him. The gate asks the same System One model the wake gate asks,
// over the same text the wake renders, and pushes only a delivery that is likely to be waiting on
// him. Skipping a push is silence, never a lost message: the SSE publish is unconditional and the
// delivery is unread in the inbox either way.
//
// It fails open in every direction. No `decisionModel`, no provider, no key, a timeout, a malformed
// answer, or any other error returns `undefined`, and the caller pushes as it always has: a phone
// that stays quiet for a message he wanted costs more than a buzz for one he did not.
//
// The question and the threshold are measured against this domain's own mail
// (`pana/docs/push-gate.md`): twelve wordings, two runs each, 816 deduped deliveries, 204 of them
// hand-labeled, and the read times of every delivery as a cross-check. The first wording and
// threshold were tuned for interruption cost and went silent on mail Josh wanted; the threshold is
// now derived from a miss weighted ten times a useless push, which is what his complaint revealed.

import { decide, DecisionError, type DecisionQuestions } from "./index.ts";
import { logGateDecision } from "./log.ts";
import type { Runtime } from "../server/runtime.ts";

/** The question's name in the request and the answer map. */
export const PUSH_QUESTION = "delivery_needs_push";

/**
 * Push only at or above this probability, and stay quiet below it.
 *
 * Derived, not guessed. On the 204 hand-labeled deliveries a missed push now costs ten times a
 * useless one: Josh said the gate pushed him nothing, so silence is the expensive error. At 10:1 the
 * cost optimum sits at 0.03 for every wording measured, which is too low to be a gate at all, so the
 * threshold follows the second rule for this case: the highest cut that still recalls 85% of the
 * wanted pushes. For the wording below that crossing is 0.09 (0.10 recalls 0.84, 0.09 recalls 0.86).
 *
 * What it buys, measured: 151.5 of 204 pushes (74%), precision 0.27, recall 0.86. On the 13 hours of
 * live traffic that prompted the change it would have pushed 8 of 15 instead of 1.
 */
export const PUSH_GATE_THRESHOLD = 0.09;

/**
 * The threshold this panad uses (PSH-11): `gates.push` when config sets it, the default otherwise.
 * Read per decision, so an edit plus a restart is the whole tune; panad reads config once at startup.
 */
export function pushThreshold(runtime: Runtime): number {
	return runtime.config.gates?.push ?? PUSH_GATE_THRESHOLD;
}

/**
 * The question, in the wording that measured best against what Josh actually wants pushed.
 *
 * It started as the plain interruption question plus "a pull request open and waiting on their
 * merge, a decision only they can make, or work blocked on them", which ranked mail at 0.81 AUC. That
 * wording went silent on the shapes he complained about: a short "yes, here is the thing you asked
 * for" scored 0.002, because it reads as a report. The clause that fixed it, and the one line below
 * that pays for itself, is "a result they asked to see, including an answer to their question or a
 * piece of work they asked for that is now done or ready": it lifts those four messages from
 * 0.002/0.018/0.004/0.042 to 0.438/0.867/0.165/0.047. The same clause widens what the gate keeps,
 * which is the trade this file documents rather than hides.
 */
const QUESTION: DecisionQuestions[string] = {
	type: "noul",
	instructions:
		"Does this message need to interrupt the recipient now with a notification on their phone? Answer yes when waiting until they next open the app would cost them something: a pull request waiting on their merge, a decision only they can make, work blocked on them, or a result they asked to see, including an answer to their question or a piece of work they asked for that is now done or ready. Answer no for unrequested status reports, routine checks that found nothing, and courtesies.",
	criteria: {
		true: "Waiting would delay something the recipient has to do or decide, or a result they asked to see that is now ready.",
		false: "An unrequested status report, a routine check that found nothing, or a courtesy.",
	},
};

export interface PushDecision {
	probability: number;
	/** False when the delivery is below the threshold, which skips the push. */
	push: boolean;
}

/** Delivery id to its decision. A delivery absent from the plan pushes as before. */
export type PushGatePlan = ReadonlyMap<string, PushDecision>;

/**
 * Decide whether each delivery of one message pushes. Returns `undefined` when the gate is off or
 * cannot answer, which means every delivery pushes as it does today.
 *
 * A failure is logged rather than swallowed: a wrong endpoint or a missing key otherwise looks
 * exactly like a gate that keeps answering "push", and nobody sees it (`messageId` makes the line
 * findable).
 */
export async function planPushGate(
	runtime: Runtime,
	text: string,
	deliveryIds: readonly string[],
	messageId?: string,
): Promise<PushGatePlan | undefined> {
	if (!runtime.config.decisionModel || deliveryIds.length === 0) return undefined;
	let probability: number;
	try {
		const answers = await decide(runtime, text, { [PUSH_QUESTION]: QUESTION });
		probability = answers[PUSH_QUESTION]!;
	} catch (error) {
		// PSH-09: fail open. The failure is the absence of a plan, and the caller pushes normally.
		const reason = error instanceof DecisionError ? `${error.code}: ${error.message}` : String((error as Error).message ?? error);
		logGateDecision(runtime, {
			event: "push.gate",
			...(messageId === undefined ? {} : { messageId }),
			decision: "failed",
			error: reason,
		});
		return undefined;
	}
	const push = probability >= pushThreshold(runtime);
	const plan = new Map<string, PushDecision>();
	for (const deliveryId of deliveryIds) plan.set(deliveryId, { probability, push });
	return plan;
}
