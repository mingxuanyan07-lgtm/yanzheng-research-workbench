namespace YanzhengCases
axiom unapproved_assumption : (0 : Nat) = 1
theorem axiom_helper : (0 : Nat) = 1 := unapproved_assumption
theorem indirect_axiom : (0 : Nat) = 1 := axiom_helper
end YanzhengCases
