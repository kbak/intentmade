// The controller validates evidence and supplies current facts.
// These policies use no temporal clauses and introduce no expiry.

@id("intentmade_validation_complete")
permit (principal, action == IntentMade::Action::"validation:complete", resource)
when {
    context.input.tests_passed
    && context.input.review_verdict == "PASS"
    && context.input.browser_passed
    && context.input.traceability_passed
};

@id("intentmade_issue_eligible")
permit (principal, action == IntentMade::Action::"issue:eligible", resource)
when {
    context.input.state == "open"
    && !context.input.is_pull_request
    && context.input.assignees == 0
    && (context.input.intake == "manual" || !context.input.label_required || context.input.label_present)
};

@id("intentmade_issue_approval")
permit (principal, action == IntentMade::Action::"issue:approval", resource)
when {
    !context.input.has_changes || context.input.latest_change_at < context.input.approved_at
};

@id("intentmade_pr_inspect")
permit (principal, action == IntentMade::Action::"pr:inspect", resource)
when {
    context.input.state == "open" && !context.input.draft
};

@id("intentmade_ci_accept")
permit (principal, action == IntentMade::Action::"ci:accept", resource)
when {
    !context.input.results.isEmpty()
    && context.input.accepted_results.containsAll(context.input.results)
    && context.input.accepted_results.containsAll(context.input.head_results)
    && context.input.matched_patterns.containsAll(context.input.required_patterns)
};

@id("intentmade_feedback_accept")
permit (principal, action == IntentMade::Action::"feedback:accept", resource)
when {
    context.input.login != "" && !context.input.generated
    && (context.input.kind != "comment" || context.input.mentioned)
    && ((context.input.bot && context.input.allowed_bots.contains(context.input.login))
        || (!context.input.bot && ["write", "maintain", "admin"].contains(context.input.permission)))
};

@id("intentmade_resume_task")
permit (principal, action == IntentMade::Action::"resume:task", resource)
when {
    ["NEEDS_INPUT", "FAILED", "PUBLICATION_FAILED"].contains(context.input.status)
};

@id("intentmade_resume_event")
permit (principal, action == IntentMade::Action::"resume:event", resource)
when {
    context.input.source == "user" && context.input.kind == "MessageEvent"
    && context.input.role == "user" && context.input.event_id != context.input.answer_id
};

@id("intentmade_resume_answer")
permit (principal, action == IntentMade::Action::"resume:answer", resource)
when {
    context.input.event_at > context.input.question_at
    && context.input.explicit && context.input.answer_present
};

@id("intentmade_resume_dispatch")
permit (principal, action == IntentMade::Action::"resume:dispatch", resource)
when {
    context.input.scheduler_enabled
};
