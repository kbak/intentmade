//! Controller-owned, stateless authorization over a private pipe.
use cedar_policy::{
    Authorizer, Context, Decision, EntityUid, PolicySet, Request, Schema, ValidationMode, Validator,
};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    error::Error,
    io::{self, Read},
    str::FromStr,
};

const POLICY: &str = include_str!("../policy.cedar");
const SCHEMA: &str = include_str!("../schema.cedarschema");
const ENGINE_VERSION: &str = "4.13.0";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    protocol: u32,
    request_id: String,
    run: String,
    task: String,
    attempt: i64,
    source_identity: String,
    action: String,
    facts: serde_json::Value,
}

fn authorize(input: &Input, policy: &str, schema: &str) -> Result<bool, Box<dyn Error>> {
    if input.protocol != 3
        || input.attempt < 0
        || [
            &input.request_id,
            &input.run,
            &input.task,
            &input.source_identity,
        ]
        .iter()
        .any(|s| s.trim().is_empty())
    {
        return Err("invalid request identity or protocol".into());
    }
    let (schema, _) = Schema::from_cedarschema_str(schema)?;
    let policies = PolicySet::from_str(policy)?;
    if !Validator::new(schema.clone())
        .validate(&policies, ValidationMode::Strict)
        .validation_passed()
    {
        return Err("policy validation failed".into());
    }
    let action = EntityUid::from_type_name_and_id(
        "IntentMade::Action".parse()?,
        input.action.parse()?,
    );
    let resource =
        EntityUid::from_type_name_and_id("IntentMade::Run".parse()?, input.run.parse()?);
    let mut facts = input
        .facts
        .as_object()
        .ok_or("facts must be an object")?
        .clone();
    for (name, value) in [
        ("task", serde_json::json!(input.task)),
        ("attempt", serde_json::json!(input.attempt)),
        ("source_identity", serde_json::json!(input.source_identity)),
    ] {
        if facts.insert(name.to_owned(), value).is_some() {
            return Err("facts must not override controller identity".into());
        }
    }
    // The published SDK validates action-specific types, required fields and
    // extra fields directly against the schema. No second fact contract.
    let context = Context::from_json_value(
        serde_json::json!({"input": facts}),
        Some((&schema, &action)),
    )?;
    let request = Request::new(
        "IntentMade::Controller::\"factory\"".parse()?,
        action,
        resource,
        context,
        Some(&schema),
    )?;
    let response = Authorizer::new().is_authorized(&request, &policies, &schema.action_entities()?);
    // Cedar can allow when a different policy errors. Our contract blocks any
    // evaluation error, even when another matching permit would allow.
    if response.diagnostics().errors().next().is_some() {
        return Err("policy evaluation failed".into());
    }
    Ok(response.decision() == Decision::Allow)
}

fn run() -> Result<(), Box<dyn Error>> {
    if std::env::args().len() != 1 {
        return Err("no arguments expected".into());
    }
    let mut bytes = Vec::new();
    io::stdin().take(128 * 1024 + 1).read_to_end(&mut bytes)?;
    if bytes.len() > 128 * 1024 {
        return Err("request too large".into());
    }
    let input: Input = serde_json::from_slice(&bytes)?;
    let allowed = authorize(&input, POLICY, SCHEMA)?;
    println!(
        "{}",
        serde_json::json!({
            "protocol": 3, "request_id": input.request_id, "action": input.action,
            "engine_version": ENGINE_VERSION,
            "policy_sha256": format!("{:x}", Sha256::digest(format!("{SCHEMA}\n{POLICY}"))),
            "allowed": allowed
        })
    );
    Ok(())
}

fn main() {
    if run().is_err() {
        // Facts and provider text never enter stderr or public task failures.
        eprintln!("Cedar authorization failed");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input() -> Input {
        Input {
            protocol: 3,
            request_id: "request".into(),
            run: "run".into(),
            task: "task".into(),
            attempt: 0,
            source_identity: "source".into(),
            action: "resume:answer".into(),
            facts: serde_json::json!({"event_at": i64::MAX, "question_at": 1, "explicit": true, "answer_present": true}),
        }
    }

    #[test]
    fn evaluation_error_blocks_even_with_another_allow() {
        let policy = r#"permit(principal, action == IntentMade::Action::"resume:answer", resource);
            permit(principal, action == IntentMade::Action::"resume:answer", resource)
            when { context.input.event_at + context.input.question_at > 0 };"#;
        assert!(authorize(&input(), policy, SCHEMA).is_err());
    }

    #[test]
    fn invalid_policy_is_rejected() {
        let policy = r#"permit(principal, action == IntentMade::Action::"resume:answer", resource)
            when { context.input.missing };"#;
        assert!(authorize(&input(), policy, SCHEMA).is_err());
    }
}
