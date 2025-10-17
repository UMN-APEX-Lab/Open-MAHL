module full_adder (
    input  wire a,
    input  wire b,
    input  wire cin,
    output wire sum,
    output wire cout
);

    // Logic for sum and carry-out
    assign sum  = a ^ b ^ cin;          // XOR operation for sum
    assign cout = (a & b) | (b & cin) | (a & cin);  // Carry-out computation

endmodule