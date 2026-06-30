`timescale 1ns/1ps
// Golden (trusted, human-written) testbench for `barrel_shifter`.
//
// It is the authoritative oracle: it exhaustively checks all
// 256 (data_in) x 8 (shift_amount) x 2 (direction) = 4096 input combinations
// against the spec, computing the expected value directly from the spec formulas.
// It prints exactly one marker: "TEST PASSED" or "TEST FAILED: ...".
module tb_barrel_shifter_golden;
    reg  [7:0] data_in;
    reg  [2:0] shift_amount;
    reg        direction;
    wire [7:0] data_out;
    reg  [7:0] expected;
    integer di, sa, dr, errors;

    barrel_shifter dut (
        .data_in(data_in), .shift_amount(shift_amount),
        .direction(direction), .data_out(data_out)
    );

    initial begin
        errors = 0;
        for (dr = 0; dr < 2; dr = dr + 1)
          for (sa = 0; sa < 8; sa = sa + 1)
            for (di = 0; di < 256; di = di + 1) begin
                data_in = di[7:0]; shift_amount = sa[2:0]; direction = dr[0];
                #1;
                // Expected value straight from the spec:
                //   direction 0 -> left,  direction 1 -> right (logical, zero-fill).
                expected = (dr == 0) ? (data_in << shift_amount)
                                     : (data_in >> shift_amount);
                if (data_out !== expected) begin
                    errors = errors + 1;
                    if (errors <= 10)
                        $display("MISMATCH dir=%0d amt=%0d data_in=%08b => got %08b exp %08b",
                                 direction, shift_amount, data_in, data_out, expected);
                end
            end
        if (errors == 0) $display("TEST PASSED");
        else $display("TEST FAILED: %0d/4096 mismatches", errors);
        $finish;
    end
endmodule
