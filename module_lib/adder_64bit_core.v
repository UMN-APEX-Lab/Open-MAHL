module adder_64bit_core (
    input  [63:0] A,
    input  [63:0] B,
    output [63:0] Sum
);

    wire [63:0] carry;

    // Connect the first full_adder with cin=0
    full_adder fa0 (
        .a(A[0]),
        .b(B[0]),
        .cin(1'b0),
        .sum(Sum[0]),
        .cout(carry[0])
    );

    // Generate full_adder instances for bits 1 to 63
    genvar i;
    generate
        for (i = 1; i < 64; i = i + 1) begin : adder_loop
            full_adder fa (
                .a(A[i]),
                .b(B[i]),
                .cin(carry[i-1]),
                .sum(Sum[i]),
                .cout(carry[i])
            );
        end
    endgenerate

endmodule