module full_adder (
    input  wire a,
    input  wire b,
    input  wire cin,
    output wire sum,
    output wire cout
);

    // Compute the sum using XOR operation
    assign sum = a ^ b ^ cin;

    // Compute the carry-out using majority logic
    assign cout = (a & b) | (b & cin) | (a & cin);

endmodule