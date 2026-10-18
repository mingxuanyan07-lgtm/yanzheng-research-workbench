import Lean.Elab.Command
import Lean.PrettyPrinter
import Lean.Elab.ElabRules
import Lean.Elab.Declaration
import Lean.Elab.Tactic.Rfl
import Lean.Elab.Tactic.Basic
import Lean.Util.CollectAxioms

/- This reviewed command audits declarations; it does not accept user programs. -/
open Lean Elab Command Meta

namespace YanzhengAudit

structure Closure where
  seen : NameSet := {}
  axioms : NameSet := {}
  unsafeNames : NameSet := {}
  partialNames : NameSet := {}
  extraTrust : NameSet := {}
  missing : NameSet := {}
  primitives : NameSet := {}

def approvedAxioms : Array Name := #[``propext, ``Classical.choice, ``Quot.sound]

def extraTrustName (n : Name) : Bool :=
  #["Lean.ofReduceBool", "Lean.ofReduceNat", "Lean.reduceBool", "Lean.reduceNat",
    "Lean.trustCompiler"].contains n.toString ||
    n.toString.startsWith "Lean.ofReduce" || n.toString.contains "_native_decide"

def kind (c : ConstantInfo) : String :=
  match c with
  | .thmInfo _ => "theorem"
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "definition"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quotient_primitive"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

/- Traverse declaration types as well as values; recursor rules and inductive
   constructors are included. Lean's official collectAxioms is merged below,
   including its imported-module axiom summaries in Lean 4.33.1. -/
partial def visit (env : Kernel.Environment) (n : Name) : StateM Closure Unit := do
  if (← get).seen.contains n then return
  modify fun s => { s with seen := s.seen.insert n }
  let some c := env.find? n | do
    modify fun s => { s with missing := s.missing.insert n }
    return
  if c.isUnsafe then
    modify fun s => { s with unsafeNames := s.unsafeNames.insert n }
  if c.isPartial then
    modify fun s => { s with partialNames := s.partialNames.insert n }
  if extraTrustName n then
    modify fun s => { s with extraTrust := s.extraTrust.insert n }
  if c.isAxiom then
    modify fun s => { s with axioms := s.axioms.insert n }
  let visitExpr (e : Expr) := e.getUsedConstants.forM (visit env)
  visitExpr c.type
  if let some value := c.value? (allowOpaque := true) then visitExpr value
  match c with
  | .inductInfo v => v.ctors.forM (visit env)
  | .recInfo v => v.rules.forM fun rule => visitExpr rule.rhs
  | .quotInfo _ => modify fun s => { s with primitives := s.primitives.insert n }
  | _ => pure ()

def namesJson (names : Array Name) : Json :=
  toJson (names.qsort Name.lt |>.map Name.toString)

elab "#yanzheng_audit " target:ident : command => do
  let targetName := target.getId
  let env ← getEnv
  let kernelEnv := env.checked.get
  let some decl := kernelEnv.find? targetName |
    throwError "Audit target is absent from the checked kernel environment: {targetName}"
  let (_, closure) := (visit kernelEnv targetName).run {}
  let officialAxioms ← collectAxioms targetName
  let axioms := officialAxioms.foldl (fun acc n => acc.insert n) closure.axioms
  let allAxioms := axioms.toArray
  let trusted := allAxioms.filter approvedAxioms.contains
  let unapproved := allAxioms.filter fun n => !approvedAxioms.contains n && n != ``sorryAx
  let extra := allAxioms.foldl (fun acc n => if extraTrustName n then acc.insert n else acc) closure.extraTrust
  let printed ← liftTermElabM <| withOptions
      (fun opts => opts.setBool `pp.all true |>.setBool `pp.fullNames true |>.setBool `pp.universes true) do
    return (← ppExpr decl.type).pretty
  let record := Json.mkObj [
    ("schema_version", toJson "1.0"),
    ("target", toJson targetName.toString),
    ("elaborated_type", toJson printed),
    ("declaration_kind", toJson (kind decl)),
    ("axioms", namesJson allAxioms),
    ("trusted_axioms", namesJson trusted),
    ("unapproved_axioms", namesJson unapproved),
    ("contains_sorry", toJson (axioms.contains ``sorryAx)),
    ("dependencies", namesJson closure.seen.toArray),
    ("unsafe_dependencies", namesJson closure.unsafeNames.toArray),
    ("partial_dependencies", namesJson closure.partialNames.toArray),
    ("extra_trust_dependencies", namesJson extra.toArray),
    ("missing_dependencies", namesJson closure.missing.toArray),
    ("kernel_primitive_dependencies", namesJson closure.primitives.toArray),
    ("audit_complete", toJson closure.missing.isEmpty)]
  liftIO <| IO.println ("YANZHENG_LEAN_AUDIT " ++ record.compress)

end YanzhengAudit
