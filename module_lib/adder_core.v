module adder_core (
    input  [63:0] a,
    input  [63:0] b,
    output [63:0] sum
);
    wire [63:0] carry;

    // Instantiate the first full_adder with cin as 0
    full_adder fa0 (
        .a(a[0]),
        .b(b[0]),
        .cin(1'b0),
        .sum(sum[0]),
        .cout(carry[0])
    );

    // Generate the remaining full_adder instances
    genvar i;
    generate
        for (i = 1; i < 64; i = i + 1) begin : adder_loop
            full_adder fa (
                .a(a[i]),
                .b(b[i]),
                .cin(carry[i-1]),
                .sum(sum[i]),
                .cout(carry[i])
            );
        end
    endgenerate

endmodule