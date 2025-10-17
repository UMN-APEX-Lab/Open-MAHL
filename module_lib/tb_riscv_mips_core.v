`timescale 1ns/1ps

module riscv_mips_core_tb;

    // Inputs
    reg [31:0] instruction;
    reg clk;
    reg reset;

    // Outputs
    wire [31:0] result;

    // Internal registers for expected results
    reg [31:0] expected_result;

    // Instantiate the DUT (Device Under Test)
    riscv_mips_core dut (
        .instruction(instruction),
        .clk(clk),
        .reset(reset),
        .result(result)
    );

    // Clock generation
    initial clk = 0;
    always #5 clk = ~clk;

    // Test stimulus
    initial begin
        // Initialize signals
        instruction = 32'b0;
        expected_result = 32'b0;
        reset = 1;

        // Apply reset
        #10;
        reset = 0;

        // Wait for a few clock cycles for DUT to stabilize
        #20;

        // Test case 1: Simple RISC-V ADD instruction
        $display("Running Test Case 1: Simple RISC-V ADD");
        instruction = 32'b0000000_00001_00010_000_00011_0110011; // ADD x3, x1, x2
        expected_result = 32'h00000003; // Example result for ADD
        #10;
        if (result !== expected_result) begin
            $display("TEST FAILED: Incorrect result for RISC-V ADD. Expected: %h, Got: %h", expected_result, result);
            $finish;
        end

        // Test case 2: Simple MIPS ADD instruction
        $display("Running Test Case 2: Simple MIPS ADD");
        instruction = 32'b000000_00001_00010_00011_00000_100000; // ADD $3, $1, $2
        expected_result = 32'h00000003; // Example result for ADD
        #10;
        if (result !== expected_result) begin
            $display("TEST FAILED: Incorrect result for MIPS ADD. Expected: %h, Got: %h", expected_result, result);
            $finish;
        end

        // Test case 3: RISC-V SUB instruction
        $display("Running Test Case 3: RISC-V SUB");
        instruction = 32'b0100000_00001_00010_000_00011_0110011; // SUB x3, x1, x2
        expected_result = 32'hFFFFFFFE; // Assuming x1 = 2, x2 = 1 for SUB
        #10;
        if (result !== expected_result) begin
            $display("TEST FAILED: Incorrect result for RISC-V SUB. Expected: %h, Got: %h", expected_result, result);
            $finish;
        end

        // Test case 4: MIPS SUB instruction
        $display("Running Test Case 4: MIPS SUB");
        instruction = 32'b000000_00001_00010_00011_00000_100010; // SUB $3, $1, $2
        expected_result = 32'hFFFFFFFE; // Assuming $1 = 2, $2 = 1 for SUB
        #10;
        if (result !== expected_result) begin
            $display("TEST FAILED: Incorrect result for MIPS SUB. Expected: %h, Got: %h", expected_result, result);
            $finish;
        end

        // Add more comprehensive test cases with varying instructions

        // Final check
        $display("TEST PASSED");
        $finish;
    end

endmodule
