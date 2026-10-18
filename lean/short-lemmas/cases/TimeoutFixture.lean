namespace YanzhengCases
/- Fixed, finite CPU computation. External wall-time termination interrupts
   this large pure Lean evaluation. There are no file/network operations,
   sleeps or user parameters; #eval would print one Nat only after completion. -/
#eval Nat.fold 1000000000 (fun _ _ acc => acc + 1) 0
theorem timeout_fixture : True := True.intro
end YanzhengCases
