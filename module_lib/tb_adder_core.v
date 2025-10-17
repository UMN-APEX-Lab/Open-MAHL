`timescale 1ns/1ps

module tb_adder_core;

    // Inputs
    reg [63:0] a;
    reg [63:0] b;

    // Outputs
    wire [63:0] sum;

    // DUT instantiation
    adder_core dut (
        .a(a),
        .b(b),
        .sum(sum)
    );

    // Task for checking the results
    task check_result;
        input [63:0] expected_sum;
        if (sum !== expected_sum) begin
            $display("TEST FAILED: Expected sum = %h, but got %h", expected_sum, sum);
            $finish;
        end else begin
            $display("Test passed for a = %h, b = %h, sum = %h", a, b, sum);
        end
    endtask

    // Test stimulus
    initial begin
        // Test case 1: Zero addition
        a = 64'd0;
        b = 64'd0;
        #10;
        check_result(64'd0);

        // Test case 2: Simple addition
        a = 64'h1;
        b = 64'h1;
        #10;
        check_result(64'h2);

        // Test case 3: Carry propagation
        a = 64'hFFFFFFFFFFFFFFFF;
        b = 64'h1;
        #10;
        check_result(64'h0);

        // Test case 4: Random values
        a = 64'h123456789ABCDEF0;
        b = 64'h0FEDCBA987654321;
        #10;
        check_result(64'h2222222211111111);

        // Test case 5: Maximum values
        a = 64'hFFFFFFFFFFFFFFFF;
        b = 64'hFFFFFFFFFFFFFFFF;
        #10;
        check_result(64'hFFFFFFFFFFFFFFFE);

        // All tests passed
        $display("TEST PASSED");
        $finish;
    end

endmodule