`timescale 1ns/1ps

module tb_adder_64bit_core;

    // Inputs
    reg [63:0] A;
    reg [63:0] B;

    // Outputs
    wire [63:0] Sum;

    // Instantiate the DUT (Device Under Test)
    adder_64bit_core uut (
        .A(A),
        .B(B),
        .Sum(Sum)
    );

    // Task to apply stimulus and check result
    task apply_and_check;
        input [63:0] a;
        input [63:0] b;
        input [63:0] expected_sum;
        begin
            A = a;
            B = b;
            #10; // Wait for the result to settle

            if (Sum !== expected_sum) begin
                $display("TEST FAILED: A = %h, B = %h, Expected Sum = %h, Got = %h", a, b, expected_sum, Sum);
                $finish;
            end else begin
                $display("Test passed for A = %h, B = %h, Sum = %h", a, b, Sum);
            end
        end
    endtask

    // Testbench logic
    initial begin
        // Apply reset if necessary (Not needed for this adder)
        
        // Displaying test start
        $display("Starting testbench for adder_64bit_core");

        // Test case 1: Add two zero numbers
        apply_and_check(64'h0, 64'h0, 64'h0);

        // Test case 2: Add zero and a number
        apply_and_check(64'h0, 64'h1, 64'h1);

        // Test case 3: Add a number and zero
        apply_and_check(64'h1, 64'h0, 64'h1);

        // Test case 4: Add two small numbers
        apply_and_check(64'd15, 64'd10, 64'd25);

        // Test case 5: Add two large numbers
        apply_and_check(64'hFFFFFFFFFFFFFFFF, 64'h1, 64'h0); // Overflow case

        // Test case 6: Random values
        apply_and_check(64'h123456789ABCDEF0, 64'h0FEDCBA987654321, 64'h2222222211111111);

        // Test case 7: Another random case
        apply_and_check(64'hAAAAAAAAAAAAAAAA, 64'h5555555555555555, 64'hFFFFFFFFFFFFFFFF);

        // All tests passed
        $display("TEST PASSED");
        $finish;
    end

endmodule