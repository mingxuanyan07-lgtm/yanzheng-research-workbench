namespace YanzhengCases
/- Explicitly exercises the legacy compiler/interpreter trust axiom, without
   calling a native compiler or constructing arbitrary generated programs. -/
theorem native_decide_trust : True := Lean.trustCompiler
end YanzhengCases
